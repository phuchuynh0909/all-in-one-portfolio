from datetime import datetime, timedelta
from typing import Optional

import math
import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, Query
from clickhouse_connect.driver import Client

from app.db.clickhouse import get_clickhouse_client

router = APIRouter(prefix="/future", tags=["future"])


def _roofing_filter(
    series: np.ndarray,
    hp_period: int = 48,
    lp_period: int = 10,
) -> np.ndarray:
    """
    John Ehlers' Roofing Filter (Cycle Analytics for Traders, 2013)

    Stage 1 – High-Pass (2-pole): removes cycles LONGER than hp_period
    Stage 2 – Super Smoother (2-pole Butterworth): removes cycles SHORTER than lp_period
    """
    n = len(series)

    angle_hp = math.radians(0.707 * 360 / hp_period)
    alpha1 = (math.cos(angle_hp) + math.sin(angle_hp) - 1) / math.cos(angle_hp)
    k1, k2, k3 = (1 - alpha1 / 2) ** 2, 2 * (1 - alpha1), (1 - alpha1) ** 2

    hp = np.zeros(n)
    for i in range(2, n):
        hp[i] = (
            k1 * (series[i] - 2 * series[i - 1] + series[i - 2])
            + k2 * hp[i - 1]
            - k3 * hp[i - 2]
        )

    a1 = math.exp(-math.sqrt(2) * math.pi / lp_period)
    b1 = 2 * a1 * math.cos(math.radians(math.sqrt(2) * 180 / lp_period))
    c2, c3 = b1, -(a1**2)
    c1 = 1 - c2 - c3

    ss = np.zeros(n)
    for i in range(2, n):
        ss[i] = c1 * (hp[i] + hp[i - 1]) / 2 + c2 * ss[i - 1] + c3 * ss[i - 2]

    return ss


def _compute_bsi(
    buy_vol: np.ndarray,
    sell_vol: np.ndarray,
    kappa: float,
    hp_period: int,
    lp_period: int,
    min_periods: int = 10,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Compute BSI, roofing-filtered BSI, and expanding Z-score normalized BSI.

    1. BSI[i]      = BSI[i-1]*exp(-kappa) + (buy_volume[i] - sell_volume[i])
    2. bsi_rf[i]   = roofing_filter(BSI)  — detrended + smoothed
    3. bsi_norm[i] = expanding Z-score of bsi_rf (NaN during warmup)

    Returns (bsi, bsi_rf, bsi_norm).
    """
    decay = np.exp(-kappa)
    dv = buy_vol.astype(float) - sell_vol.astype(float)

    bsi = np.empty_like(dv)
    val = 0.0
    for i in range(len(dv)):
        val = val * decay + dv[i]
        bsi[i] = val

    bsi_rf = _roofing_filter(bsi, hp_period=hp_period, lp_period=lp_period)

    s = pd.Series(bsi_rf)
    exp_mean = s.expanding(min_periods=min_periods).mean()
    exp_std = s.expanding(min_periods=min_periods).std()
    bsi_norm = ((s - exp_mean) / exp_std).to_numpy()

    return bsi, bsi_rf, bsi_norm


def _compute_kama(prices: np.ndarray, period: int) -> np.ndarray:
    n = len(prices)
    kama = np.full(n, np.nan)
    if n <= period:
        return kama

    fast_sc = 2.0 / (2 + 1)
    slow_sc = 2.0 / (30 + 1)

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
    kappa: float = Query(0.1),
    hp_period: int = Query(45),
    lp_period: int = Query(11),
    ch: Client = Depends(get_clickhouse_client),
):
    """
    Get 5-minute OHLC data with BSI indicators for a futures symbol.
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
            "indicators": {"bsi": [], "bsi_rf": [], "bsi_norm": []},
        }

    timestamps = [r[0] for r in rows]
    opens = [r[1] for r in rows]
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    closes = [r[4] for r in rows]
    volumes = [r[5] for r in rows]
    buy_vols = np.array([r[6] for r in rows], dtype=float)
    sell_vols = np.array([r[7] for r in rows], dtype=float)

    bsi, bsi_rf, bsi_norm = _compute_bsi(
        buy_vols,
        sell_vols,
        kappa=kappa,
        hp_period=hp_period,
        lp_period=lp_period,
    )

    close_arr = np.array(closes, dtype=float)
    kama_21 = _compute_kama(close_arr, period=21)
    kama_200 = _compute_kama(close_arr, period=200)

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
            "bsi_rf": _safe(bsi_rf),
            "bsi_norm": _safe(bsi_norm),
            "kama_21": _safe(kama_21),
            "kama_200": _safe(kama_200),
        },
    }
