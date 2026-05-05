from datetime import datetime, timedelta
from typing import Optional
import os
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from clickhouse_connect.driver import Client

from app.db.clickhouse import get_clickhouse_client

router = APIRouter(prefix="/future", tags=["future"])


def _compute_bsi(
    buy_vol: np.ndarray,
    sell_vol: np.ndarray,
    kappa: float,
) -> np.ndarray:
    """Hawkes BSI: BSI[i] = BSI[i-1]*exp(-kappa) + (buy_volume[i] - sell_volume[i])"""
    decay = np.exp(-kappa)
    dv = buy_vol.astype(float) - sell_vol.astype(float)
    bsi = np.empty_like(dv)
    val = 0.0
    for i in range(len(dv)):
        val = val * decay + dv[i]
        bsi[i] = val
    return bsi


def _compute_quantile_bands(
    bsi: np.ndarray,
    lookback: int = 200,
    q_lo_pct: float = 5.0,
    q_hi_pct: float = 95.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Rolling quantile bands (no lookahead) over the last `lookback` bars."""
    s = pd.Series(bsi)
    q_lo = s.rolling(lookback, min_periods=2).quantile(q_lo_pct / 100.0).to_numpy()
    q_hi = s.rolling(lookback, min_periods=2).quantile(q_hi_pct / 100.0).to_numpy()
    return q_lo, q_hi


def _compute_kama(
    prices: np.ndarray,
    period: int = 10,
    fast: int = 2,
    slow: int = 30,
) -> np.ndarray:
    n = len(prices)
    kama = np.full(n, np.nan)
    if n <= period:
        return kama

    fast_sc = 2.0 / (fast + 1)
    slow_sc = 2.0 / (slow + 1)

    kama[period - 1] = prices[period - 1]
    for i in range(period, n):
        direction = abs(prices[i] - prices[i - period])
        volatility = np.sum(np.abs(np.diff(prices[i - period : i + 1])))
        er = direction / volatility if volatility > 0 else 0.0
        sc = (er * (fast_sc - slow_sc) + slow_sc) ** 2
        kama[i] = kama[i - 1] + sc * (prices[i] - kama[i - 1])

    return kama


_VALID_TIMEFRAMES: dict[str, str | None] = {
    "5m":  None,
    "15m": "15 MINUTE",
    "30m": "30 MINUTE",
    "1h":  "1 HOUR",
}


@router.get("/ohlc-5m/{symbol}")
async def get_ohlc_5m(
    symbol: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    timeframe: str = Query("5m"),
    kappa: float = Query(0.2),
    quantile_lookback: int = Query(50),
    q_lo_pct: float = Query(5.0),
    q_hi_pct: float = Query(95.0),
    kama_period: int = Query(20),
    ch: Client = Depends(get_clickhouse_client),
):
    """
    Get OHLC data (5m/15m/30m/1h) with Hawkes BSI + rolling quantile bands for a futures symbol.
    Higher timeframes are re-aggregated from the ohlc_5m table via ClickHouse.
    """
    if timeframe not in _VALID_TIMEFRAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid timeframe '{timeframe}'. Valid: {list(_VALID_TIMEFRAMES)}",
        )

    start_clause = ""
    end_clause = ""
    if start_date:
        start_clause = f"AND ts >= toDateTime('{start_date}', 'Asia/Ho_Chi_Minh')"
    else:
        default_start = (datetime.now() - timedelta(days=360)).strftime("%Y-%m-%d")
        start_clause = f"AND ts >= toDateTime('{default_start}', 'Asia/Ho_Chi_Minh')"
    if end_date:
        end_clause = f"AND ts <= toDateTime('{end_date}', 'Asia/Ho_Chi_Minh')"

    interval = _VALID_TIMEFRAMES[timeframe]

    if interval is None:
        query = f"""
            SELECT
                formatDateTime(ts, '%Y-%m-%dT%H:%i:%S', 'Asia/Ho_Chi_Minh') AS timestamp,
                open, high, low, close, volume, buy_volume, sell_volume
            FROM default.ohlc_5m FINAL
            WHERE symbol = '{symbol}'
              {start_clause}
              {end_clause}
            ORDER BY ts ASC
        """
    else:
        query = f"""
            SELECT
                formatDateTime(
                    toStartOfInterval(ts, INTERVAL {interval}, 'Asia/Ho_Chi_Minh'),
                    '%Y-%m-%dT%H:%i:%S', 'Asia/Ho_Chi_Minh'
                ) AS timestamp,
                argMin(open, ts)  AS open,
                max(high)         AS high,
                min(low)          AS low,
                argMax(close, ts) AS close,
                sum(volume)       AS volume,
                sum(buy_volume)   AS buy_volume,
                sum(sell_volume)  AS sell_volume
            FROM default.ohlc_5m FINAL
            WHERE symbol = '{symbol}'
              {start_clause}
              {end_clause}
            GROUP BY toStartOfInterval(ts, INTERVAL {interval}, 'Asia/Ho_Chi_Minh')
            ORDER BY timestamp ASC
        """

    result = ch.query(query)
    rows = result.result_rows

    if not rows:
        return {
            "symbol": symbol,
            "timestamps": [],
            "ohlc": {"open": [], "high": [], "low": [], "close": []},
            "volume": {"total": [], "buy": [], "sell": []},
            "indicators": {"bsi": [], "q_lo": [], "q_hi": [], "kama": []},
        }

    timestamps = [r[0] for r in rows]
    opens = [r[1] for r in rows]
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    closes = [r[4] for r in rows]
    volumes = [r[5] for r in rows]
    buy_vols = np.array([r[6] for r in rows], dtype=float)
    sell_vols = np.array([r[7] for r in rows], dtype=float)

    bsi = _compute_bsi(buy_vols, sell_vols, kappa=kappa)
    q_lo, q_hi = _compute_quantile_bands(
        bsi,
        lookback=quantile_lookback,
        q_lo_pct=q_lo_pct,
        q_hi_pct=q_hi_pct,
    )

    close_arr = np.array(closes, dtype=float)
    kama = _compute_kama(close_arr, period=kama_period)

    def _safe(arr: np.ndarray) -> list:
        return [None if np.isnan(v) else float(v) for v in arr]

    return {
        "symbol": symbol,
        "timestamps": timestamps,
        "ohlc": {"open": opens, "high": highs, "low": lows, "close": closes},
        "volume": {
            "total": volumes,
            "buy": buy_vols.tolist(),
            "sell": sell_vols.tolist(),
        },
        "indicators": {
            "bsi": _safe(bsi),
            "q_lo": _safe(q_lo),
            "q_hi": _safe(q_hi),
            "kama": _safe(kama),
        },
    }


# ── RL exit model cache ────────────────────────────────────────────────────
_rl_model_cache: dict = {}


def _rl_exit_model_path(symbol: str) -> Path:
    """Path to the saved PPO checkpoint (default: backend/models/rl_exit_{symbol}.zip)."""
    model_dir = Path(os.environ.get(
        "RL_MODEL_DIR",
        str(Path(__file__).parents[4] / "models"),
    ))
    print(model_dir / f"rl_exit_{symbol}.zip")
    return model_dir / f"rl_exit_{symbol}.zip"


def _load_rl_model(symbol: str):
    """Load (and cache) the PPO exit model. Raises HTTPException if file or deps missing."""
    if symbol in _rl_model_cache:
        return _rl_model_cache[symbol]

    path = _rl_exit_model_path(symbol)
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                f"RL model file not found: {path}. "
                f"Save rl_exit_{symbol}.zip there or set RL_MODEL_DIR."
            ),
        )

    try:
        from stable_baselines3 import PPO
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail=f"stable-baselines3 is required for RL exits (pip install backend deps): {e}",
        ) from e

    model = PPO.load(str(path))
    _rl_model_cache[symbol] = model
    return model


def _compute_avwap(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    vols: np.ndarray,
    window: int,
) -> tuple[np.ndarray, np.ndarray]:
    """AVWAP anchored to rolling pivot high / low (causal, no lookahead)."""
    n = len(highs)
    avwap_ph = np.full(n, np.nan)
    avwap_pl = np.full(n, np.nan)
    prev_ph = prev_pl = -1
    ph_tv = ph_v = pl_tv = pl_v = 0.0

    for i in range(n):
        lo = max(0, i - window + 1)
        tp = (highs[i] + lows[i] + closes[i]) / 3.0
        vol = vols[i]

        ph = lo + int(np.argmax(highs[lo: i + 1]))
        pl = lo + int(np.argmin(lows[lo:  i + 1]))

        if ph != prev_ph:
            s = slice(ph, i + 1)
            tp_s = (highs[s] + lows[s] + closes[s]) / 3.0
            ph_tv = float(np.dot(tp_s, vols[s]))
            ph_v  = float(vols[s].sum())
            prev_ph = ph
        else:
            ph_tv += tp * vol
            ph_v  += vol

        if pl != prev_pl:
            s = slice(pl, i + 1)
            tp_s = (highs[s] + lows[s] + closes[s]) / 3.0
            pl_tv = float(np.dot(tp_s, vols[s]))
            pl_v  = float(vols[s].sum())
            prev_pl = pl
        else:
            pl_tv += tp * vol
            pl_v  += vol

        if ph_v > 0:
            avwap_ph[i] = ph_tv / ph_v
        if pl_v > 0:
            avwap_pl[i] = pl_tv / pl_v

    return avwap_ph, avwap_pl


def _generate_entries(
    bsi: np.ndarray,
    q_lo: np.ndarray,
    q_hi: np.ndarray,
    closes: np.ndarray,
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    kama: np.ndarray,
    sl_bars: int = 10,
) -> list[dict]:
    """Mirror the frontend computeSignals() entry logic (long above q_hi, short below q_lo)."""
    n = len(bsi)
    entries: list[dict] = []
    pos = 0
    in_upper_zone = in_lower_zone = False
    last_below_price = np.nan

    for i in range(n - 1):
        b, lo, hi = bsi[i], q_lo[i], q_hi[i]
        if np.isnan(b) or np.isnan(lo) or np.isnan(hi):
            continue

        close = closes[i]
        k = kama[i]
        kama_ok = np.isnan(k)

        if pos == 1 and b < lo:
            pos = 0
        if pos == -1 and b > hi:
            pos = 0

        if b >= hi:
            in_lower_zone = False
            if not in_upper_zone:
                in_upper_zone = True
        elif b <= lo:
            in_upper_zone = False
            last_below_price = close
            if not in_lower_zone:
                in_lower_zone = True
        else:
            in_upper_zone = in_lower_zone = False

        if pos != 0:
            continue

        j = i + 1
        if b >= hi and in_upper_zone:
            price_change = close - last_below_price
            if (not np.isnan(last_below_price)
                    and price_change > 0
                    and (kama_ok or close > k)):
                win = max(0, i - sl_bars + 1)
                entries.append({
                    'entry_bar':   j,
                    'direction':   1,
                    'entry_price': float(opens[j]),
                    'sl_price':    float(np.min(lows[win: j])),
                })
                pos = 1
                in_upper_zone = False

        if b <= lo and in_lower_zone:
            if kama_ok or close < k:
                win = max(0, i - sl_bars + 1)
                entries.append({
                    'entry_bar':   j,
                    'direction':   -1,
                    'entry_price': float(opens[j]),
                    'sl_price':    float(np.max(highs[win: j])),
                })
                pos = -1
                in_lower_zone = False

    return entries


def _run_rl_inference(
    model,
    entries: list[dict],
    timestamps: list[str],
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    bsi: np.ndarray,
    q_lo: np.ndarray,
    q_hi: np.ndarray,
    kama: np.ndarray,
    avwap_ph_s: np.ndarray,
    avwap_pl_s: np.ndarray,
    avwap_ph_l: np.ndarray,
    avwap_pl_l: np.ndarray,
    max_hold: int = 60,
    sl_bars: int = 10,
) -> list[dict]:
    n = len(closes)
    results = []

    for t in entries:
        eb = t['entry_bar']
        ep = t['entry_price']
        d  = t['direction']
        sl = t['sl_price']

        peak_upnl = 0.0
        rl_exit   = min(eb + max_hold, n - 1)
        rl_type   = 'max_hold'

        for i in range(eb, min(eb + max_hold + 1, n)):
            bars_held = i - eb

            sl_hit = (
                not np.isnan(sl)
                and bars_held <= sl_bars
                and ((d == 1 and lows[i] <= sl) or (d == -1 and highs[i] >= sl))
            )
            if sl_hit:
                rl_exit = i; rl_type = 'sl'; break

            if bars_held >= max_hold or i >= n - 1:
                rl_exit = i; rl_type = 'max_hold'; break

            c     = closes[i]
            upnl  = (c - ep) / ep * d * 100.0
            peak_upnl = max(peak_upnl, upnl)

            band    = q_hi[i] - q_lo[i]
            bsi_pos = float(np.clip(
                (bsi[i] - q_lo[i]) / band if band > 1e-10 else 0.0, -2.0, 3.0))
            gate = (c - kama[i]) / c * d * 100.0 if not np.isnan(kama[i]) else 0.0
            ret1 = (c / closes[max(0, i - 1)] - 1.0) * d * 100.0
            ret5 = (c / closes[max(0, i - 5)] - 1.0) * d * 100.0

            def _ad(av: float) -> float:
                return (c - av) / c * d * 100.0 if not np.isnan(av) else 0.0

            obs = np.array([
                upnl, bars_held / max_hold, bsi_pos, gate,
                ret1, ret5,
                1.0 if bars_held < sl_bars else 0.0,
                max(0.0, peak_upnl - upnl),
                _ad(avwap_ph_s[i]), _ad(avwap_pl_s[i]),
                _ad(avwap_ph_l[i]), _ad(avwap_pl_l[i]),
            ], dtype=np.float32)

            action, _ = model.predict(obs, deterministic=True)
            if int(action) == 1:
                rl_exit = i; rl_type = 'agent'; break

        # Rule-based exit (long: BSI < q_lo, short: BSI > q_hi) for comparison
        rule_exit = min(eb + max_hold, n - 1)
        rule_type = 'max_hold'
        for i in range(eb, min(eb + max_hold + 1, n)):
            bars_held = i - eb
            sl_hit = (
                not np.isnan(sl)
                and bars_held <= sl_bars
                and ((d == 1 and lows[i] <= sl) or (d == -1 and highs[i] >= sl))
            )
            if sl_hit:
                rule_exit = i; rule_type = 'sl'; break
            if d == 1 and bsi[i] < q_lo[i]:
                rule_exit = i; rule_type = 'bsi'; break
            if d == -1 and bsi[i] > q_hi[i]:
                rule_exit = i; rule_type = 'bsi'; break
            if bars_held >= max_hold:
                rule_exit = i; rule_type = 'max_hold'; break

        def _ep(bar: int) -> float:
            return float(closes[min(bar, n - 1)])

        def _pnl(bar: int) -> float:
            return round((_ep(bar) - ep) / ep * d * 100, 4)

        results.append({
            'entry_bar':       eb,
            'entry_time':      timestamps[min(eb, len(timestamps) - 1)],
            'direction':       d,
            'entry_price':     ep,
            'rl_exit_bar':     rl_exit,
            'rl_exit_time':    timestamps[min(rl_exit, len(timestamps) - 1)],
            'rl_exit_price':   _ep(rl_exit),
            'rl_pnl_pct':      _pnl(rl_exit),
            'rl_exit_type':    rl_type,
            'rule_exit_bar':   rule_exit,
            'rule_exit_time':  timestamps[min(rule_exit, len(timestamps) - 1)],
            'rule_exit_price': _ep(rule_exit),
            'rule_pnl_pct':    _pnl(rule_exit),
            'rule_exit_type':  rule_type,
        })

    return results


@router.get("/rl-exits/{symbol}")
async def get_rl_exits(
    symbol: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    kappa: float = Query(0.4),
    quantile_lookback: int = Query(20),
    q_lo_pct: float = Query(5.0),
    q_hi_pct: float = Query(95.0),
    kama_period: int = Query(10),
    sl_bars: int = Query(10),
    max_hold: int = Query(60),
    avwap_short: int = Query(50),
    avwap_long: int = Query(200),
    ch: Client = Depends(get_clickhouse_client),
):
    """Run RL exit model over the same entry signals as the chart and return per-trade results."""
    model = _load_rl_model(symbol)

    start_clause = ""
    end_clause   = ""
    if start_date:
        start_clause = f"AND ts >= toDateTime('{start_date}', 'Asia/Ho_Chi_Minh')"
    else:
        default_start = (datetime.now() - timedelta(days=360)).strftime("%Y-%m-%d")
        start_clause  = f"AND ts >= toDateTime('{default_start}', 'Asia/Ho_Chi_Minh')"
    if end_date:
        end_clause = f"AND ts <= toDateTime('{end_date}', 'Asia/Ho_Chi_Minh')"

    query = f"""
        SELECT
            formatDateTime(ts, '%Y-%m-%dT%H:%i:%S', 'Asia/Ho_Chi_Minh') AS timestamp,
            open, high, low, close, volume, buy_volume, sell_volume
        FROM default.ohlc_5m FINAL
        WHERE symbol = '{symbol}'
          {start_clause}
          {end_clause}
        ORDER BY ts ASC
    """
    rows = ch.query(query).result_rows
    if not rows:
        return {"symbol": symbol, "trades": []}

    timestamps = [r[0] for r in rows]
    opens      = np.array([r[1] for r in rows], dtype=float)
    highs      = np.array([r[2] for r in rows], dtype=float)
    lows       = np.array([r[3] for r in rows], dtype=float)
    closes     = np.array([r[4] for r in rows], dtype=float)
    buy_vols   = np.array([r[6] for r in rows], dtype=float)
    sell_vols  = np.array([r[7] for r in rows], dtype=float)
    vols       = buy_vols + sell_vols

    bsi         = _compute_bsi(buy_vols, sell_vols, kappa=kappa)
    q_lo, q_hi  = _compute_quantile_bands(
        bsi, lookback=quantile_lookback, q_lo_pct=q_lo_pct, q_hi_pct=q_hi_pct)
    kama        = _compute_kama(closes, period=kama_period)

    avwap_ph_s, avwap_pl_s = _compute_avwap(highs, lows, closes, vols, avwap_short)
    avwap_ph_l, avwap_pl_l = _compute_avwap(highs, lows, closes, vols, avwap_long)

    entries = _generate_entries(
        bsi, q_lo, q_hi, closes, opens, highs, lows, kama, sl_bars=sl_bars)

    trades = _run_rl_inference(
        model, entries, timestamps,
        closes, highs, lows,
        bsi, q_lo, q_hi, kama,
        avwap_ph_s, avwap_pl_s,
        avwap_ph_l, avwap_pl_l,
        max_hold=max_hold, sl_bars=sl_bars,
    )

    return {"symbol": symbol, "trades": trades}
