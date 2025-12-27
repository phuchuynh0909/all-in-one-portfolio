import numpy as np
from numba import njit, prange
import numba as nb

def avwap(close: np.array, high: np.array, low: np.array, volume: np.array, is_highest: bool = True, window: int = 200) -> np.array:
    n_rows = len(close)
    avwap_arr = np.full(n_rows, np.nan)
    
    prev_anchor_index = -1
    cum_tp_vol = 0.0  # Cumulative (typical_price * volume)
    cum_vol = 0.0     # Cumulative volume

    for i in range(window - 1, n_rows):
        # Find extreme price in window
        window_start = i - window + 1
        close_window = close[window_start:i + 1]
        if is_highest:
            extreme_idx = np.argmax(close_window)
        else:
            extreme_idx = np.argmin(close_window)
        
        anchor_index = window_start + extreme_idx
        
        # If anchor changed, reset cumulative sums from new anchor
        if anchor_index != prev_anchor_index:
            prev_anchor_index = anchor_index
            cum_tp_vol = 0.0
            cum_vol = 0.0
            # Recalculate from anchor to current bar
            for k in range(anchor_index, i + 1):
                tp = (close[k] + high[k] + low[k]) / 3
                cum_tp_vol += tp * volume[k]
                cum_vol += volume[k]
        else:
            # Anchor unchanged, just add current bar
            tp = (close[i] + high[i] + low[i]) / 3
            cum_tp_vol += tp * volume[i]
            cum_vol += volume[i]
        
        # Calculate AVWAP for current bar
        if cum_vol > 0:
            avwap_arr[i] = cum_tp_vol / cum_vol

    return avwap_arr

@njit(parallel=True)
def avwap_func_nb(close_arr, high_arr, low_arr, volume_arr, is_highest: bool = True, window: int = 200):
    n_rows, n_cols = close_arr.shape
    avwap_arr = np.full((n_rows, n_cols), np.nan)

    for col in nb.prange(n_cols):
        close = close_arr[:, col]
        high = high_arr[:, col]
        low = low_arr[:, col]
        volume = volume_arr[:, col]
        
        prev_anchor_index = -1
        cum_tp_vol = 0.0  # Cumulative (typical_price * volume)
        cum_vol = 0.0     # Cumulative volume

        for i in range(window - 1, n_rows):
            # Find extreme price in window
            window_start = i - window + 1
            close_window = close[window_start:i + 1]
            if is_highest:
                extreme_idx = np.argmax(close_window)
            else:
                extreme_idx = np.argmin(close_window)
            
            anchor_index = window_start + extreme_idx
            
            # If anchor changed, reset cumulative sums from new anchor
            if anchor_index != prev_anchor_index:
                prev_anchor_index = anchor_index
                cum_tp_vol = 0.0
                cum_vol = 0.0
                # Recalculate from anchor to current bar
                for k in range(anchor_index, i + 1):
                    tp = (close[k] + high[k] + low[k]) / 3
                    cum_tp_vol += tp * volume[k]
                    cum_vol += volume[k]
            else:
                # Anchor unchanged, just add current bar
                tp = (close[i] + high[i] + low[i]) / 3
                cum_tp_vol += tp * volume[i]
                cum_vol += volume[i]
            
            # Calculate AVWAP for current bar
            if cum_vol > 0:
                avwap_arr[i, col] = cum_tp_vol / cum_vol

    return avwap_arr