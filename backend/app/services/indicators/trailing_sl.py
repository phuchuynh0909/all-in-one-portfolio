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