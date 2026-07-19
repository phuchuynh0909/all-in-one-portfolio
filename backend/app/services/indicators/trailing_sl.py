import numpy as np
from numba import njit, prange
import numba as nb

def trailing_sl(close: np.array, atr: np.array, atr_multiplier: float = 1.8) -> np.array:
    
    sl_price = atr * atr_multiplier
    trail = np.full(close.shape, np.nan, dtype=np.float64)

    for i in range(1, trail.shape[0]):
        if np.isnan(close[i]):
            trail[i] = np.nan
            continue
        src = close[i]
        src_prev = close[i-1]
        trail_prev = trail[i-1]
        iff_1 = src - sl_price[i] if src > trail_prev else src + sl_price[i]
        iff_2 = min(trail_prev, src + sl_price[i]) if src < trail_prev and src_prev < trail_prev else iff_1
        trail[i] = max(trail_prev, src - sl_price[i]) if src > trail_prev and src_prev > trail_prev else iff_2
    
    return trail

@njit(parallel=True)
def atr_trailing_nb(close, atr_val, atr_multiplier: float = 1.8):
    sl = (atr_val * atr_multiplier)
    trail = np.full(close.shape, np.nan, dtype=np.float64)

    for col in nb.prange (trail.shape[1]):
        for i in range(1, trail.shape[0]):
            if np.isnan(close[i, col]):
                trail[i, col] = np.nan
                continue
            src = close[i, col]
            src_prev = close[i-1, col]
            trail_prev = trail[i-1, col]
            iff_1 = src - sl[i, col] if src > trail_prev else src + sl[i, col]
            iff_2 = min(trail_prev, src + sl[i, col]) if src < trail_prev and src_prev < trail_prev else iff_1
            trail[i, col] = max(trail_prev, src - sl[i, col]) if src > trail_prev and src_prev > trail_prev else iff_2
    return trail


@njit(parallel=True)
def pearson_r_2d(close, window):
    """
    Rolling Pearson correlation of price vs. a linear time index over `window`
    bars, computed per column. Measures how *linear* (trending) the price path
    is: r -> +1 clean uptrend, r -> -1 clean downtrend, r ~ 0 choppy/ranging.

    Returns a 2D array (same shape as close), NaN for the warmup region and
    for windows containing NaN closes.
    """
    n, m = close.shape
    out = np.full((n, m), np.nan, dtype=np.float64)
    if window < 2 or window > n:
        return out

    # x = 0..window-1 is constant across all bars — precompute its stats once.
    x_mean = (window - 1) / 2.0
    sxx = 0.0
    for k in range(window):
        dx = k - x_mean
        sxx += dx * dx

    for col in nb.prange(m):
        for i in range(window - 1, n):
            start = i - window + 1
            y_sum = 0.0
            valid = True
            for k in range(window):
                v = close[start + k, col]
                if np.isnan(v):
                    valid = False
                    break
                y_sum += v
            if not valid:
                continue
            y_mean = y_sum / window
            sxy = 0.0
            syy = 0.0
            for k in range(window):
                dy = close[start + k, col] - y_mean
                sxy += (k - x_mean) * dy
                syy += dy * dy
            denom = np.sqrt(sxx * syy)
            if denom > 0.0:
                out[i, col] = sxy / denom
    return out


@njit(parallel=True)
def atr_trailing_adaptive_nb(close, atr_val, mult):
    """
    ATR trailing stop with a *per-bar* multiplier `mult` (same shape as close)
    instead of a scalar. Identical trailing logic to `atr_trailing_nb`; only the
    stop distance sl = atr * mult varies bar-to-bar. Feed a Pearson-correlation
    driven multiplier here for a trend-adaptive stop (wide in clean trends,
    tight in chop).
    """
    trail = np.full(close.shape, np.nan, dtype=np.float64)

    for col in nb.prange(trail.shape[1]):
        for i in range(1, trail.shape[0]):
            if np.isnan(close[i, col]):
                trail[i, col] = np.nan
                continue
            s = atr_val[i, col] * mult[i, col]
            src = close[i, col]
            src_prev = close[i-1, col]
            trail_prev = trail[i-1, col]
            iff_1 = src - s if src > trail_prev else src + s
            iff_2 = min(trail_prev, src + s) if src < trail_prev and src_prev < trail_prev else iff_1
            trail[i, col] = max(trail_prev, src - s) if src > trail_prev and src_prev > trail_prev else iff_2
    return trail