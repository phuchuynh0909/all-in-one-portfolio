from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, Query
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


@router.get("/ohlc-5m/{symbol}")
async def get_ohlc_5m(
    symbol: str,
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    kappa: float = Query(0.2),
    quantile_lookback: int = Query(50),
    q_lo_pct: float = Query(5.0),
    q_hi_pct: float = Query(95.0),
    kama_period: int = Query(20),
    ch: Client = Depends(get_clickhouse_client),
):
    """
    Get 5-minute OHLC data with Hawkes BSI + rolling quantile bands for a futures symbol.
    """
    start_clause = ""
    end_clause = ""
    if start_date:
        start_clause = f"AND ts >= toDateTime('{start_date}', 'Asia/Ho_Chi_Minh')"
    else:
        default_start = (datetime.now() - timedelta(days=360)).strftime("%Y-%m-%d")
        start_clause = f"AND ts >= toDateTime('{default_start}', 'Asia/Ho_Chi_Minh')"
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
