"""Prefect flow: sync active Covered Warrant (CW) OHLC + Greeks to ClickHouse.

CW filter  : symbol length == 8, starts with 'C', MetaStock file readable
Underlying : symbol[1:4]  (e.g. CVNM2001 → VNM)
Metadata   : fetched from DNSE API (strike, expiry, conversion_ratio, option_type)
Greeks     : Black-Scholes. IV back-solved from market price; falls back to hist-vol.

Run:
    python sync_cw.py
    python sync_cw.py --full-refresh
    python sync_cw.py --deploy
"""

from __future__ import annotations

from typing import Any
from pathlib import Path
import asyncio
import gc
import argparse
import os
import math

import httpx
import numpy as np
import pandas as pd
from prefect import flow, task
from custom_metastock2pd import metastock_read, metastock_read_master
from clickhouse_driver import Client  # type: ignore


# ── constants ─────────────────────────────────────────────────────────────────

DNSE_STOCK_DIR    = r"D:\dnse\eod\stock"
DNSE_CW_DETAIL_URL = "https://api-bo.dnse.com.vn/senses-api/covered-warrants"
RISK_FREE_RATE    = float(os.getenv("CW_RISK_FREE_RATE", "0.05"))
HIST_VOL_WINDOW   = int(os.getenv("CW_HIST_VOL_WINDOW", "20"))
MAX_DAYS_INCR     = int(os.getenv("CW_MAX_DAYS_INCR", "20"))


# ── env / connection helpers ──────────────────────────────────────────────────

def _get_env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _get_ch_client() -> Client:
    host = _get_env("CLICKHOUSE_HOST", "localhost")
    port = int(_get_env("CLICKHOUSE_PORT", "9010"))
    try:
        return Client(
            host=host, port=port,
            user=_get_env("CLICKHOUSE_USER", "kyostyle1"),
            password=_get_env("CLICKHOUSE_PASSWORD", "kyostyle1"),
            database=_get_env("CLICKHOUSE_DB", "default"),
        )
    except Exception as e:
        raise RuntimeError(
            f"ClickHouse connection failed at {host}:{port} — "
            f"set CLICKHOUSE_PORT to the native TCP port (not HTTP 8123). Error: {e}"
        ) from e


# ── metastock helpers ─────────────────────────────────────────────────────────

def _get_df_emaster(dir_path: str) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for entry in os.listdir(dir_path):
        folder_path = os.path.join(dir_path, entry)
        if os.path.isdir(folder_path):
            parts.append(metastock_read_master(folder_path, encoding="latin1"))
        else:
            print("Not a folder:", folder_path)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _filter_active_cw(emaster_df: pd.DataFrame) -> pd.DataFrame:
    """Filter EMASTER to active CW symbols whose MetaStock files are still readable.

    Expired CWs keep their EMASTER entry but the data file becomes unreadable.
    Reading with fields=7, version=1 is a lightweight probe used in production.
    """
    candidates = emaster_df[
        (emaster_df["symbol"].str.len() == 8)
        & (emaster_df["symbol"].str.startswith("C"))
    ]

    active_rows: list[Any] = []
    for _, row in candidates.iterrows():
        filename = row["filename"]
        if isinstance(filename, pd.Series):
            filename = filename.iloc[0]
        try:
            metastock_read(filename, fields=7, version=1)
            active_rows.append(row)
        except Exception:
            continue  # expired or corrupt file — skip

    if not active_rows:
        return pd.DataFrame()

    cw_df = pd.DataFrame(active_rows).reset_index(drop=True)
    cw_df["underlying"] = cw_df["symbol"].str[1:4]
    expired = len(candidates) - len(cw_df)
    print(
        f"Active CWs: {len(cw_df)} "
        f"({expired} expired/unreadable filtered), "
        f"{cw_df['underlying'].nunique()} underlying stocks"
    )
    return cw_df


_PRICE_COLS = ("open", "high", "low", "close")


def _fix_price_scale(df: pd.DataFrame) -> pd.DataFrame:
    """Rescale price columns stored as raw integers and round to 2 decimal places.

    Some MetaStock sources store prices as integers (e.g. 3910 instead of 39.10).
    Any price column value > 1000 is divided by 100, then rounded to 2 dp.
    Volume is left untouched.
    """
    for col in _PRICE_COLS:
        if col not in df.columns:
            continue
        df[col] = pd.to_numeric(df[col], errors="coerce")
        mask = df[col] > 1000
        df.loc[mask, col] = df.loc[mask, col] / 1000
        df[col] = df[col].round(2)
    return df


def _load_ohlc_for_symbols(
    emaster_subset: pd.DataFrame,
    cutoff: pd.Timestamp | None,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []

    def _apply_cutoff(tick: pd.DataFrame) -> pd.DataFrame:
        tick = tick.sort_index()
        if cutoff is not None:
            tick = tick[tick.index >= cutoff]
        return tick.reset_index(names="date")

    for _, row in emaster_subset.iterrows():
        filename = row["filename"]
        if isinstance(filename, pd.Series):
            filename = filename.iloc[0]
        try:
            tick = metastock_read(filename, extra_buffer=50)
            tick = _apply_cutoff(tick)
            tick["symbol"] = row["symbol"]
            frames.append(tick)
        except Exception as e:
            print(f"Cannot read {filename}: {e}")

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    del frames
    gc.collect()
    return _fix_price_scale(df)


# ── DNSE API metadata fetch ───────────────────────────────────────────────────

def _pick_positive(*values: Any) -> float | None:
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0:
            return f
    return None


def _infer_option_style(cw_stock_type: str | None) -> str:
    normalized = (cw_stock_type or "").strip().lower()
    if normalized in {"mua", "call"}:
        return "call"
    if normalized in {"ban", "bán", "put"}:
        return "put"
    return "call"


def _normalize_strike(raw_strike: Any, stock_price: float | None) -> float | None:
    """Align strike to the same price scale as underlying (thousand VND)."""
    strike = _pick_positive(raw_strike)
    if strike is None:
        return None
    if stock_price and stock_price > 0 and strike / stock_price > 100:
        return strike / 1000.0
    return strike


async def _fetch_one_cw_detail(
    client: httpx.AsyncClient, symbol: str
) -> dict[str, Any]:
    try:
        resp = await client.get(DNSE_CW_DETAIL_URL, params={"symbol": symbol})
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, dict) and payload.get("symbol"):
            return payload
        return {}
    except Exception as e:
        print(f"  API fetch failed for {symbol}: {e}")
        return {}


async def _fetch_all_cw_details(symbols: list[str]) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await asyncio.gather(
            *[_fetch_one_cw_detail(client, s) for s in symbols]
        )


@task(log_prints=True)
def fetch_cw_metadata_from_api(
    symbols: list[str],
    underlying_latest_close: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Fetch CW metadata (strike, expiry_date, conversion_ratio, option_type) from DNSE API.

    underlying_latest_close: symbol → latest close price, used to normalize strike scale.
    Returns one row per symbol that the API returned valid data for.
    """
    if not symbols:
        return pd.DataFrame(
            columns=["symbol", "strike", "expiry_date", "conversion_ratio", "option_type"]
        )

    print(f"Fetching metadata from DNSE API for {len(symbols)} CW symbols …")
    results = asyncio.run(_fetch_all_cw_details(symbols))

    records: list[dict[str, Any]] = []
    for payload in results:
        if not payload or not payload.get("symbol"):
            continue

        sym          = str(payload["symbol"]).upper()
        base_code    = payload.get("baseStockCode") or sym[1:4]
        stock_price  = (underlying_latest_close or {}).get(base_code)
        strike       = _normalize_strike(payload.get("exercisePrice"), stock_price)
        conv_rate    = _pick_positive(payload.get("conversionRate")) or 1.0
        option_type  = _infer_option_style(payload.get("cwStockType"))

        raw_expiry = payload.get("lastTradingDate") or payload.get("lastTradingdate")
        expiry_date = None
        if raw_expiry:
            try:
                expiry_date = pd.to_datetime(
                    str(raw_expiry).replace("Z", "").split("T")[0]
                ).date()
            except Exception:
                pass

        records.append({
            "symbol":          sym,
            "strike":          strike,
            "expiry_date":     expiry_date,
            "conversion_ratio": conv_rate,
            "option_type":     option_type,
        })

    df = pd.DataFrame(records)
    ok  = df["strike"].notna().sum()
    print(f"Metadata fetched: {len(df)} symbols, {ok} with valid strike price")
    return df


# ── Greeks computation ────────────────────────────────────────────────────────

def _bs_price(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call",
) -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(S - K, 0) if option_type == "call" else max(K - S, 0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if option_type == "call":
        return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
    return K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _implied_vol(
    market_price: float, S: float, K: float, T: float, r: float,
    option_type: str = "call",
    lo: float = 0.001, hi: float = 5.0, tol: float = 1e-5, max_iter: int = 200,
) -> float | None:
    if T <= 0 or market_price <= 0:
        return None
    f = lambda v: _bs_price(S, K, T, r, v, option_type) - market_price
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        val = f(mid)
        if abs(val) < tol:
            return mid
        if f(lo) * val < 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def _bs_greeks(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call",
) -> dict[str, float]:
    intrinsic = max(S - K, 0) if option_type == "call" else max(K - S, 0)
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return dict(delta=float("nan"), gamma=float("nan"), theta=float("nan"),
                    vega=float("nan"), bs_price=intrinsic,
                    intrinsic_value=intrinsic, time_value=0.0)

    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t

    if option_type == "call":
        delta = _ncdf(d1)
        theta_share = (
            -(S * _npdf(d1) * sigma) / (2 * sqrt_t)
            - r * K * math.exp(-r * T) * _ncdf(d2)
        )
    else:
        delta = _ncdf(d1) - 1.0
        theta_share = (
            -(S * _npdf(d1) * sigma) / (2 * sqrt_t)
            + r * K * math.exp(-r * T) * _ncdf(-d2)
        )

    gamma    = _npdf(d1) / (S * sigma * sqrt_t)
    theta    = theta_share / 365.0          # daily decay
    vega     = S * _npdf(d1) * sqrt_t / 100  # per 1% vol change
    bs_price = _bs_price(S, K, T, r, sigma, option_type)

    return dict(
        delta=delta, gamma=gamma, theta=theta, vega=vega,
        bs_price=bs_price, intrinsic_value=intrinsic,
        time_value=max(bs_price - intrinsic, 0.0),
    )


def compute_cw_analytics(
    cw_ohlc: pd.DataFrame,
    underlying_ohlc: pd.DataFrame,
    cw_emaster: pd.DataFrame,
    metadata: pd.DataFrame,
    r: float = RISK_FREE_RATE,
    hist_vol_window: int = HIST_VOL_WINDOW,
) -> pd.DataFrame:
    """Join CW OHLC with underlying price + API metadata; compute Greeks per row."""
    if cw_ohlc.empty:
        return pd.DataFrame()

    cw_ohlc         = cw_ohlc.copy()
    underlying_ohlc = underlying_ohlc.copy()
    cw_ohlc["date"]         = pd.to_datetime(cw_ohlc["date"],         errors="coerce")
    underlying_ohlc["date"] = pd.to_datetime(underlying_ohlc["date"], errors="coerce")

    sym_to_under = dict(zip(cw_emaster["symbol"], cw_emaster["underlying"]))
    cw_ohlc["underlying"] = cw_ohlc["symbol"].map(sym_to_under)

    und_close = (
        underlying_ohlc[["date", "symbol", "close"]]
        .rename(columns={"symbol": "underlying", "close": "underlying_close"})
    )
    cw_ohlc = cw_ohlc.merge(und_close, on=["date", "underlying"], how="left")
    cw_ohlc["underlying_close"] = cw_ohlc["underlying_close"].round(2)

    # Historical vol per underlying (rolling window, then merge by symbol+date)
    hv_records: list[dict] = []
    for und_sym, grp in underlying_ohlc.groupby("symbol"):
        grp = grp.sort_values("date")
        log_ret = np.log(grp["close"] / grp["close"].shift(1))
        hv = log_ret.rolling(hist_vol_window).std() * math.sqrt(252)
        for d, v in zip(grp["date"], hv):
            hv_records.append({"underlying": und_sym, "date": d, "hist_vol": v})
    hv_df   = pd.DataFrame(hv_records)
    cw_ohlc = cw_ohlc.merge(hv_df, on=["date", "underlying"], how="left")
    del hv_df

    # Merge API metadata
    if not metadata.empty:
        cw_ohlc = cw_ohlc.merge(
            metadata[["symbol", "strike", "expiry_date", "conversion_ratio", "option_type"]],
            on="symbol", how="left",
        )
        cw_ohlc["expiry_date"]      = pd.to_datetime(cw_ohlc["expiry_date"], errors="coerce")
        cw_ohlc["days_to_expiry"]   = (cw_ohlc["expiry_date"] - cw_ohlc["date"]).dt.days
        cw_ohlc["conversion_ratio"] = pd.to_numeric(
            cw_ohlc.get("conversion_ratio", 1.0), errors="coerce"
        ).fillna(1.0)
        cw_ohlc["option_type"] = cw_ohlc.get("option_type", "call").fillna("call")
    else:
        cw_ohlc["strike"] = None
        cw_ohlc["expiry_date"] = None
        cw_ohlc["days_to_expiry"] = None
        cw_ohlc["conversion_ratio"] = 1.0
        cw_ohlc["option_type"] = "call"

    # Row-by-row Greeks
    delta_l, gamma_l, theta_l, vega_l = [], [], [], []
    iv_l, intrinsic_l, time_val_l, bs_price_l = [], [], [], []

    for row in cw_ohlc.itertuples(index=False):
        S     = getattr(row, "underlying_close", None)
        K     = getattr(row, "strike",           None)
        dte   = getattr(row, "days_to_expiry",   None)
        hv    = getattr(row, "hist_vol",         None)
        otype = getattr(row, "option_type",      "call") or "call"
        mkt   = row.close
        ratio = float(getattr(row, "conversion_ratio", 1.0) or 1.0)

        def _valid(v: Any) -> bool:
            return v is not None and not (isinstance(v, float) and math.isnan(v))

        T  = float(dte) / 365.0 if _valid(dte) and float(dte) > 0 else None

        iv = None
        if _valid(S) and _valid(K) and T is not None and mkt > 0:
            iv = _implied_vol(mkt * ratio, float(S), float(K), T, r, otype)

        use_sigma = iv or (float(hv) if _valid(hv) and float(hv) > 0 else None)

        if _valid(S) and _valid(K) and T is not None and use_sigma:
            g = _bs_greeks(float(S), float(K), T, r, use_sigma, otype)
            delta_l.append(g["delta"]          / ratio)
            gamma_l.append(g["gamma"]          / ratio)
            theta_l.append(g["theta"]          / ratio)
            vega_l.append(g["vega"]            / ratio)
            intrinsic_l.append(g["intrinsic_value"] / ratio)
            time_val_l.append(g["time_value"]  / ratio)
            bs_price_l.append(g["bs_price"]    / ratio)
        else:
            for lst in (delta_l, gamma_l, theta_l, vega_l,
                        intrinsic_l, time_val_l, bs_price_l):
                lst.append(float("nan"))
        iv_l.append(iv)

    cw_ohlc["iv"]              = iv_l
    cw_ohlc["delta"]           = delta_l
    cw_ohlc["gamma"]           = gamma_l
    cw_ohlc["theta"]           = theta_l
    cw_ohlc["vega"]            = vega_l
    cw_ohlc["intrinsic_value"] = intrinsic_l
    cw_ohlc["time_value"]      = time_val_l
    cw_ohlc["bs_price"]        = bs_price_l
    cw_ohlc["premium_pct"] = np.where(
        cw_ohlc["bs_price"] > 0,
        (cw_ohlc["close"] - cw_ohlc["bs_price"]) / cw_ohlc["bs_price"] * 100,
        float("nan"),
    )

    return cw_ohlc


# ── ClickHouse table + insert ─────────────────────────────────────────────────

def _ensure_cw_table_exists(client: Client, database: str, table: str) -> None:
    try:
        client.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
    except Exception as e:
        raise RuntimeError(f"ClickHouse handshake failed: {e}") from e
    client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {database}.{table} (
            date              Date,
            symbol            String,
            underlying        String,
            open              Float64,
            high              Float64,
            low               Float64,
            close             Float64,
            volume            Float64,
            underlying_close  Float64,
            strike            Nullable(Float64),
            expiry_date       Nullable(Date),
            days_to_expiry    Nullable(Int32),
            conversion_ratio  Float64,
            hist_vol          Nullable(Float64),
            iv                Nullable(Float64),
            delta             Nullable(Float64),
            gamma             Nullable(Float64),
            theta             Nullable(Float64),
            vega              Nullable(Float64),
            intrinsic_value   Nullable(Float64),
            time_value        Nullable(Float64),
            bs_price          Nullable(Float64),
            premium_pct       Nullable(Float64),
            ver               DateTime64(3) DEFAULT now64(3)
        )
        ENGINE = ReplacingMergeTree(ver)
        PARTITION BY toYYYYMM(date)
        ORDER BY (symbol, date)
        """
    )


def _insert_cw_analytics_to_clickhouse(
    df: pd.DataFrame, batch_size: int = 10000
) -> int:
    database = _get_env("CLICKHOUSE_DB", "default")
    table    = _get_env("CLICKHOUSE_CW_TABLE", "cw_analytics")
    client   = _get_ch_client()
    _ensure_cw_table_exists(client, database, table)

    cols = [
        "date", "symbol", "underlying", "open", "high", "low", "close", "volume",
        "underlying_close", "strike", "expiry_date", "days_to_expiry",
        "conversion_ratio", "hist_vol", "iv", "delta", "gamma", "theta", "vega",
        "intrinsic_value", "time_value", "bs_price", "premium_pct",
    ]
    cols    = [c for c in cols if c in df.columns]
    df_out  = df[cols].copy()

    def _nullable(v: Any, cast: type = float) -> Any:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        return cast(v)

    inserted = 0
    for start in range(0, len(df_out), batch_size):
        chunk = df_out.iloc[start : start + batch_size]
        rows  = []
        for r in chunk.itertuples(index=False, name=None):
            d = dict(zip(cols, r))
            exp = d.get("expiry_date")
            rows.append((
                pd.to_datetime(d["date"]).date(),
                str(d["symbol"]),
                str(d.get("underlying", "")),
                float(d.get("open")   or 0),
                float(d.get("high")   or 0),
                float(d.get("low")    or 0),
                float(d.get("close")  or 0),
                float(d.get("volume") or 0),
                float(d.get("underlying_close") or 0),
                _nullable(d.get("strike")),
                pd.to_datetime(exp).date() if exp and not pd.isna(exp) else None,
                _nullable(d.get("days_to_expiry"), int),
                float(d.get("conversion_ratio") or 1.0),
                _nullable(d.get("hist_vol")),
                _nullable(d.get("iv")),
                _nullable(d.get("delta")),
                _nullable(d.get("gamma")),
                _nullable(d.get("theta")),
                _nullable(d.get("vega")),
                _nullable(d.get("intrinsic_value")),
                _nullable(d.get("time_value")),
                _nullable(d.get("bs_price")),
                _nullable(d.get("premium_pct")),
            ))
        if not rows:
            continue
        client.execute(
            f"INSERT INTO {database}.{table} "
            f"(date, symbol, underlying, open, high, low, close, volume, "
            f"underlying_close, strike, expiry_date, days_to_expiry, "
            f"conversion_ratio, hist_vol, iv, delta, gamma, theta, vega, "
            f"intrinsic_value, time_value, bs_price, premium_pct) VALUES",
            rows,
            types_check=False,
        )
        inserted += len(rows)
    return inserted


# ── Prefect tasks ─────────────────────────────────────────────────────────────

@task(log_prints=True)
def extract_cw_data(
    stock_dir: str = DNSE_STOCK_DIR,
    full_refresh: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load CW OHLC + underlying OHLC from MetaStock. Returns (cw_ohlc, underlying_ohlc, cw_emaster)."""
    cutoff = (
        None if full_refresh
        else pd.Timestamp.today().normalize() - pd.Timedelta(days=MAX_DAYS_INCR)
    )
    print(
        "Full refresh — all history" if full_refresh
        else f"Incremental — from {cutoff.date()} onwards"
    )

    all_emaster = _get_df_emaster(stock_dir)
    cw_emaster  = _filter_active_cw(all_emaster)
    if cw_emaster.empty:
        print("No active CW symbols found — exiting.")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    underlying_symbols  = set(cw_emaster["underlying"].dropna().unique())
    underlying_emaster  = all_emaster[all_emaster["symbol"].isin(underlying_symbols)]

    print(f"Loading OHLC for {len(cw_emaster)} CWs …")
    cw_ohlc = _load_ohlc_for_symbols(cw_emaster, cutoff)

    print(f"Loading OHLC for {len(underlying_emaster)} underlying stocks …")
    underlying_ohlc = _load_ohlc_for_symbols(underlying_emaster, cutoff)

    print(f"CW rows: {len(cw_ohlc):,}  Underlying rows: {len(underlying_ohlc):,}")
    return cw_ohlc, underlying_ohlc, cw_emaster


@task(log_prints=True)
def compute_and_store_greeks(
    cw_ohlc: pd.DataFrame,
    underlying_ohlc: pd.DataFrame,
    cw_emaster: pd.DataFrame,
    metadata: pd.DataFrame,
) -> int:
    """Compute Black-Scholes Greeks and insert into ClickHouse cw_analytics table."""
    if cw_ohlc.empty:
        print("No CW data — skipping.")
        return 0

    print("Computing Greeks …")
    analytics_df = compute_cw_analytics(
        cw_ohlc=cw_ohlc,
        underlying_ohlc=underlying_ohlc,
        cw_emaster=cw_emaster,
        metadata=metadata,
    )
    del cw_ohlc, underlying_ohlc
    gc.collect()

    if analytics_df.empty:
        print("Analytics DataFrame is empty — nothing to insert.")
        return 0

    print(f"Inserting {len(analytics_df):,} rows to ClickHouse …")
    inserted = _insert_cw_analytics_to_clickhouse(analytics_df)
    del analytics_df
    gc.collect()
    print(f"Inserted {inserted:,} rows → cw_analytics")
    return inserted


# ── Prefect flow ──────────────────────────────────────────────────────────────

@flow(log_prints=True)
def sync_cw_analytics_pipeline(
    stock_dir: str = DNSE_STOCK_DIR,
    full_refresh: bool = False,
) -> None:
    """Flow: MetaStock CW → DNSE API metadata → Greeks → ClickHouse cw_analytics."""

    cw_ohlc, underlying_ohlc, cw_emaster = extract_cw_data(
        stock_dir=stock_dir, full_refresh=full_refresh,
    )

    # Build latest underlying close map for strike price normalisation
    underlying_latest: dict[str, float] = {}
    if not underlying_ohlc.empty:
        latest = (
            underlying_ohlc.sort_values("date")
            .groupby("symbol")["close"]
            .last()
        )
        underlying_latest = latest.to_dict()

    metadata = fetch_cw_metadata_from_api(
        symbols=list(cw_emaster["symbol"]) if not cw_emaster.empty else [],
        underlying_latest_close=underlying_latest,
    )

    compute_and_store_greeks(
        cw_ohlc=cw_ohlc,
        underlying_ohlc=underlying_ohlc,
        cw_emaster=cw_emaster,
        metadata=metadata,
    )


# ── plain-Python entry point ──────────────────────────────────────────────────

def main(stock_dir: str = DNSE_STOCK_DIR, full_refresh: bool = False) -> None:
    print("=== Step 1: Extract CW + underlying OHLC from MetaStock ===")
    cw_ohlc, underlying_ohlc, cw_emaster = extract_cw_data.fn(
        stock_dir=stock_dir, full_refresh=full_refresh,
    )

    underlying_latest: dict[str, float] = {}
    if not underlying_ohlc.empty:
        underlying_latest = (
            underlying_ohlc.sort_values("date")
            .groupby("symbol")["close"]
            .last()
            .to_dict()
        )

    print("\n=== Step 2: Fetch CW metadata from DNSE API ===")
    metadata = fetch_cw_metadata_from_api.fn(
        symbols=list(cw_emaster["symbol"]) if not cw_emaster.empty else [],
        underlying_latest_close=underlying_latest,
    )

    print("\n=== Step 3: Compute Greeks + sync to ClickHouse ===")
    inserted = compute_and_store_greeks.fn(
        cw_ohlc=cw_ohlc,
        underlying_ohlc=underlying_ohlc,
        cw_emaster=cw_emaster,
        metadata=metadata,
    )
    print(f"\nDone. Total rows inserted: {inserted:,}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync CW OHLC + Greeks to ClickHouse")
    parser.add_argument("--stock-dir",    default=DNSE_STOCK_DIR, help="MetaStock stock directory")
    parser.add_argument("--full-refresh", action="store_true",    help="Load all history instead of last N days")
    parser.add_argument("--deploy",       action="store_true",    help="Deploy as Prefect deployment")
    args = parser.parse_args()

    if args.deploy:
        sync_cw_analytics_pipeline.from_source(
            source=str(Path(__file__).parent),
            entrypoint="sync_cw.py:sync_cw_analytics_pipeline",
        ).deploy(
            name="sync-cw-analytics",
            work_pool_name="my-worker",
            cron="30 8-9 * * 1-5",  # 15:30–16:30 ICT, mon–fri
        )
    else:
        main(stock_dir=args.stock_dir, full_refresh=args.full_refresh)
