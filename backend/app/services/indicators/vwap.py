import numpy as np
from numba import njit, prange
import numba as nb

def avwap(close: np.array, high: np.array, low: np.array, volume: np.array, is_highest: bool = True, window: int = 200) -> np.array:
    """
    Calculate Anchored VWAP from the highest/lowest price point within a rolling window.
    
    Args:
        close: Close prices
        high: High prices
        low: Low prices
        volume: Volume data
        is_highest: If True, anchor at highest price; if False, anchor at lowest price
        window: Lookback window size
    
    Returns:
        Array of AVWAP values
    """
    n_rows = len(close)
    avwap_arr = np.full(n_rows, np.nan)
    
    # Handle edge cases
    if n_rows < window:
        return avwap_arr
    
    # Check for valid volume data
    if np.all(volume == 0) or np.all(np.isnan(volume)):
        return avwap_arr

    for i in range(window - 1, n_rows):
        # Find extreme price in current window
        window_start = i - window + 1
        close_window = close[window_start:i + 1]
        
        if is_highest:
            extreme_idx = np.argmax(close_window)
        else:
            extreme_idx = np.argmin(close_window)
        
        anchor_index = window_start + extreme_idx
        
        # Always calculate VWAP from anchor to current bar
        # This ensures correctness regardless of anchor changes
        cum_tp_vol = 0.0
        cum_vol = 0.0
        
        for k in range(anchor_index, i + 1):
            vol = volume[k]
            if vol > 0 and not np.isnan(vol):  # Skip zero/NaN volume bars
                tp = (close[k] + high[k] + low[k]) / 3
                cum_tp_vol += tp * vol
                cum_vol += vol
        
        # Calculate AVWAP for current bar
        if cum_vol > 0:
            avwap_arr[i] = cum_tp_vol / cum_vol

    return avwap_arr

@njit(parallel=True)
def avwap_func_nb(close_arr, high_arr, low_arr, volume_arr, is_highest: bool = True, window: int = 200):
    """
    Numba-optimized Anchored VWAP calculation for multiple symbols.
    
    Args:
        close_arr: 2D array of close prices (rows=time, cols=symbols)
        high_arr: 2D array of high prices
        low_arr: 2D array of low prices
        volume_arr: 2D array of volume data
        is_highest: If True, anchor at highest; if False, anchor at lowest
        window: Lookback window size
    
    Returns:
        2D array of AVWAP values
    """
    n_rows, n_cols = close_arr.shape
    avwap_arr = np.full((n_rows, n_cols), np.nan)

    for col in nb.prange(n_cols):
        close = close_arr[:, col]
        high = high_arr[:, col]
        low = low_arr[:, col]
        volume = volume_arr[:, col]

        for i in range(window - 1, n_rows):
            # Find extreme price in current window
            window_start = i - window + 1
            
            # Find extreme index manually (argmax/argmin on slice)
            if is_highest:
                extreme_val = close[window_start]
                extreme_idx = 0
                for j in range(1, window):
                    if close[window_start + j] > extreme_val:
                        extreme_val = close[window_start + j]
                        extreme_idx = j
            else:
                extreme_val = close[window_start]
                extreme_idx = 0
                for j in range(1, window):
                    if close[window_start + j] < extreme_val:
                        extreme_val = close[window_start + j]
                        extreme_idx = j
            
            anchor_index = window_start + extreme_idx
            
            # Calculate VWAP from anchor to current bar
            cum_tp_vol = 0.0
            cum_vol = 0.0
            
            for k in range(anchor_index, i + 1):
                vol = volume[k]
                if vol > 0:  # Skip zero volume bars
                    tp = (close[k] + high[k] + low[k]) / 3.0
                    cum_tp_vol += tp * vol
                    cum_vol += vol
            
            # Calculate AVWAP for current bar
            if cum_vol > 0:
                avwap_arr[i, col] = cum_tp_vol / cum_vol

    return avwap_arr