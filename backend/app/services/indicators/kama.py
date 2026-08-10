import numpy as np
import numba as nb


@nb.njit(cache=True)
def kama_2d(prices_np: np.ndarray, period: int = 10, fast: int = 2, slow: int = 30) -> np.ndarray:
    """
    Kaufman's Adaptive Moving Average, applied column-wise to a 2-D array.

    Efficiency Ratio  ER = |close - close[period]| / sum(|diff(close)|, period)
    Smoothing Constant SC = (ER * (fast_sc - slow_sc) + slow_sc) ** 2
    KAMA[i] = KAMA[i-1] + SC * (close[i] - KAMA[i-1])

    Trending market  (ER → 1): SC → fast_sc  (tracks quickly)
    Choppy market    (ER → 0): SC → slow_sc  (barely moves)
    """
    n, m    = prices_np.shape
    out     = np.full((n, m), np.nan)
    fast_sc = 2.0 / (fast + 1)
    slow_sc = 2.0 / (slow + 1)
    for j in range(m):
        kama = prices_np[period - 1, j]
        out[period - 1, j] = kama
        for i in range(period, n):
            direction  = abs(prices_np[i, j] - prices_np[i - period, j])
            volatility = 0.0
            for k in range(1, period + 1):
                volatility += abs(prices_np[i - k + 1, j] - prices_np[i - k, j])
            er   = direction / volatility if volatility != 0.0 else 0.0
            sc   = (er * (fast_sc - slow_sc) + slow_sc) ** 2
            kama = kama + sc * (prices_np[i, j] - kama)
            out[i, j] = kama
    return out


@nb.njit(cache=True)
def slope_flat_2d(kama_np: np.ndarray, slope_window: int, flat_threshold_pct: float) -> np.ndarray:
    """
    Boolean 2-D mask: True where KAMA slope is 'flat'.

    Flat is defined as |pct change over slope_window bars| < flat_threshold_pct.
    First slope_window rows are always False.
    """
    n, m = kama_np.shape
    out  = np.zeros((n, m), dtype=np.bool_)
    for j in range(m):
        for i in range(slope_window, n):
            prev = kama_np[i - slope_window, j]
            cur  = kama_np[i, j]
            if prev != 0.0 and not np.isnan(prev) and not np.isnan(cur):
                slope_pct = abs((cur - prev) / prev * 100.0)
                out[i, j] = slope_pct < flat_threshold_pct
    return out
