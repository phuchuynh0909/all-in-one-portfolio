"""Stock feature scanner.

Unlike the older feature-store-backed scanner, this computes every indicator
column directly from raw OHLCV (via ``stock_service._load_delta_stocks``) at
request time — no precomputed Delta Lake feature store involved. This lets the
scanner support indicators (e.g. the linreg prediction channel / Gaussian
FRAMA / Hull Butterfly Oscillator ported from notebooks/backtest_010.ipynb and
notebooks/backtest_012.ipynb) that the feature store never materialized.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import List

import numpy as np
import pandas as pd
import talib

from app.schemas.scanner import ConditionOperator, ScannerRequest, ScannerResponse, ScannerResultItem
from app.services.indicators import gaussian_frama, hull_butterfly, linreg_channel_2d, student_t_crit, trailing_sl
from app.services.stock_service import _load_delta_stocks

# backtest_010 (LR prediction channel) defaults
_REG_WINDOW = 50
_REG_CONFIDENCE = 0.88

# backtest_012 (Gaussian FRAMA + Hull Butterfly Oscillator) defaults
_GFRAMA_KWARGS = dict(gaussian_length=6, sigma=1.0, fm_len=16, upper_limit=8, lower_limit=40, atr_period=5, atr_mult=1.0)
_HBO_LENGTH = 9
_HBO_MULT = 2.5
_ATR_TRAIL_MULT = 1.8

# backtest_010 "Reclaim Entry" scan: flag a symbol for this many bars after
# close crosses above pi_lower, so a latest-bar-only scan still surfaces
# triggers from earlier in the week (mirrors the notebook's scan_reclaim LOOKBACK).
_RECLAIM_LOOKBACK = 7

# Extra calendar days of history fetched before the requested window so
# rolling indicators (EMA-200, 50-bar regression, ...) are warmed up.
_WARMUP_DAYS = 400

SCANNER_COLUMNS: List[str] = [
    "date", "symbol", "open", "high", "low", "close", "volume",
    "rsi_5", "rsi_14", "ema_10", "ema_20", "ema_50", "ema_200", "atr_10", "atr_14", "obv",
    "reg", "pi_upper", "pi_lower", "ci_upper", "ci_lower", "slope_pct", "reclaim_entry",
    "frama", "long_v", "short_v", "qb", "hso", "os", "trail",
]

_COMPARISON_OPS = (
    ConditionOperator.eq,
    ConditionOperator.ne,
    ConditionOperator.gt,
    ConditionOperator.gte,
    ConditionOperator.lt,
    ConditionOperator.lte,
)


def list_columns() -> List[str]:
    return SCANNER_COLUMNS


def _latest_trading_date(today: date | None = None) -> date:
    """Return today's date if weekday; otherwise the previous Friday.
    If currently in trading session, return the previous trading day."""
    d = today or date.today()

    # Check if we're currently in a trading session (9:00 AM - 3:00 PM)
    # If so, use previous day since today's data is not yet complete
    if today is None:  # Only check time if using current date
        now = datetime.now()
        if 9 <= now.hour < 15:  # Trading hours: 9:00 AM - 3:00 PM
            d = d - timedelta(days=1)

    # Monday=0 ... Sunday=6
    if d.weekday() == 5:  # Saturday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday
        return d - timedelta(days=2)
    return d


def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute every scannable indicator column for one symbol's OHLCV
    history. ``df`` must be sorted by date ascending."""
    out = df.reset_index(drop=True).copy()
    close = out["close"].to_numpy(dtype=np.float64)
    high = out["high"].to_numpy(dtype=np.float64)
    low = out["low"].to_numpy(dtype=np.float64)
    volume = out["volume"].to_numpy(dtype=np.float64)

    out["rsi_5"] = talib.RSI(close, timeperiod=5)
    out["rsi_14"] = talib.RSI(close, timeperiod=14)
    out["ema_10"] = talib.EMA(close, timeperiod=10)
    out["ema_20"] = talib.EMA(close, timeperiod=20)
    out["ema_50"] = talib.EMA(close, timeperiod=50)
    out["ema_200"] = talib.EMA(close, timeperiod=200)
    out["atr_10"] = talib.ATR(high, low, close, timeperiod=10)
    out["atr_14"] = talib.ATR(high, low, close, timeperiod=14)
    out["obv"] = talib.OBV(close, volume)

    # backtest_010: LR prediction channel
    t_crit = student_t_crit(_REG_WINDOW, _REG_CONFIDENCE)
    reg, slope, ci_u, ci_l, pi_u, pi_l = linreg_channel_2d(close.reshape(-1, 1), _REG_WINDOW, float(t_crit))
    reg = reg.reshape(-1)
    out["reg"] = reg
    out["ci_upper"] = ci_u.reshape(-1)
    out["ci_lower"] = ci_l.reshape(-1)
    out["pi_upper"] = pi_u.reshape(-1)
    out["pi_lower"] = pi_l.reshape(-1)
    with np.errstate(divide="ignore", invalid="ignore"):
        out["slope_pct"] = np.where(reg != 0, slope.reshape(-1) / reg * 100.0, np.nan)

    # backtest_010: Reclaim Entry Trigger — close crosses above pi_lower
    prev_close = np.roll(close, 1)
    prev_close[0] = np.nan
    pi_lower = out["pi_lower"].to_numpy(dtype=np.float64)
    prev_pi_lower = np.roll(pi_lower, 1)
    prev_pi_lower[0] = np.nan
    crossed_above = ((prev_close <= prev_pi_lower) & (close > pi_lower)).astype(np.float64)
    out["reclaim_entry"] = (
        pd.Series(crossed_above).rolling(_RECLAIM_LOOKBACK, min_periods=1).max().to_numpy()
    )

    # backtest_012: Gaussian FRAMA + Hull Butterfly Oscillator
    gframa = gaussian_frama(close, high, low, **_GFRAMA_KWARGS)
    out["frama"] = gframa["frama"].reshape(-1)
    out["long_v"] = gframa["long_v"].reshape(-1)
    out["short_v"] = gframa["short_v"].reshape(-1)
    out["qb"] = gframa["qb"].reshape(-1)

    hso, os_state = hull_butterfly(close, length=_HBO_LENGTH, mult=_HBO_MULT)
    out["hso"] = hso.reshape(-1)
    out["os"] = os_state.reshape(-1)

    out["trail"] = trailing_sl(close, out["atr_14"].to_numpy(dtype=np.float64), atr_multiplier=_ATR_TRAIL_MULT)

    return out


def _apply_conditions(df: pd.DataFrame, req: ScannerRequest) -> pd.DataFrame:
    filtered = df
    for cond in req.conditions:
        col = cond.column
        if col not in filtered.columns:
            # skip unknown column
            continue
        op = cond.operator
        val = cond.value
        # A string value matching another feature column name is treated as a
        # column-to-column comparison (e.g. close > pi_upper), not a literal —
        # needed for scanners ported from the notebooks that compare two bands.
        if isinstance(val, str) and val in filtered.columns and op in _COMPARISON_OPS:
            val = filtered[val]
        if op == ConditionOperator.eq:
            filtered = filtered[filtered[col] == val]
        elif op == ConditionOperator.ne:
            filtered = filtered[filtered[col] != val]
        elif op == ConditionOperator.gt:
            filtered = filtered[filtered[col] > val]
        elif op == ConditionOperator.gte:
            filtered = filtered[filtered[col] >= val]
        elif op == ConditionOperator.lt:
            filtered = filtered[filtered[col] < val]
        elif op == ConditionOperator.lte:
            filtered = filtered[filtered[col] <= val]
        elif op == ConditionOperator.isin:
            filtered = filtered[filtered[col].isin(val)]
        elif op == ConditionOperator.notin:
            filtered = filtered[~filtered[col].isin(val)]
        elif op == ConditionOperator.between:
            filtered = filtered[(filtered[col] >= val[0]) & (filtered[col] <= val[1])]
        elif op == ConditionOperator.contains:
            filtered = filtered[filtered[col].astype(str).str.contains(str(val), na=False)]
    return filtered


def scan(req: ScannerRequest) -> ScannerResponse:
    # Default to latest trading date if no dates provided
    if not req.start_date and not req.end_date:
        target = _latest_trading_date()
        start = pd.to_datetime(target)
        end = pd.to_datetime(target)
    else:
        start = pd.to_datetime(req.start_date) if req.start_date else None
        end = pd.to_datetime(req.end_date) if req.end_date else None

    # Fetch extra history before `start` so rolling indicators are warmed up,
    # then trim back to the requested window after computing them.
    load_start = (start - timedelta(days=_WARMUP_DAYS)) if start is not None else None

    raw = _load_delta_stocks(symbols=req.symbols or None, start=load_start, end=end)
    if raw.empty:
        return ScannerResponse(items=[], total=0)

    raw["date"] = pd.to_datetime(raw["date"])

    frames = []
    for symbol, group in raw.groupby("symbol", sort=False):
        computed = _compute_indicators(group.sort_values("date"))
        computed["symbol"] = symbol
        frames.append(computed)
    df = pd.concat(frames, ignore_index=True)

    if start is not None:
        df = df[df["date"] >= start]
    if end is not None:
        df = df[df["date"] <= end]

    df = _apply_conditions(df, req)

    if req.latest_only and {"symbol", "date"}.issubset(df.columns):
        df = df.sort_values(["symbol", "date"]).groupby("symbol", as_index=False).tail(1)

    # Only surface columns actually referenced by the request, matching the
    # previous feature-store-backed behavior (which only loaded those columns).
    requested_columns = (
        {c.column for c in req.conditions}
        | {c.value for c in req.conditions if isinstance(c.value, str)}
        | set(req.columns_to_return or [])
    )
    value_columns = [c for c in df.columns if c not in ("date", "symbol") and c in requested_columns]

    result_items: List[ScannerResultItem] = []
    for _, row in df.iterrows():
        values = {c: (None if pd.isna(row[c]) else row[c]) for c in value_columns}
        result_items.append(
            ScannerResultItem(symbol=str(row["symbol"]), date=row["date"].to_pydatetime(), values=values)
        )

    return ScannerResponse(items=result_items, total=len(result_items))
