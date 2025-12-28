from numba import njit
import numba as nb
import numpy as np

@njit
def exrem_func_nb(entries, exits):
    rows, cols = entries.shape
    result = np.full(entries.shape, False)
    for col in nb.prange(cols):
        in_position = False
        for i in range(rows):
            if np.isnan(entries[i, col]) or np.isnan(exits[i, col]):
                continue
            if entries[i, col] and not in_position:
                in_position = True
                result[i, col] = True
            elif exits[i, col] and in_position:
                in_position = False
    return result


@njit
def lowest_at_entry(low, entry):
    lowest_low = np.full(entry.shape, np.nan, dtype=np.float64)
    for col in range(lowest_low.shape[1]):
        for i in range(1, lowest_low.shape[0]):
            if np.isnan(low[i, col]):
                lowest_low[i, col] = np.nan
                continue    
            if entry[i, col]:
                lowest_low[i, col] = low[i, col]
            else:
                lowest_low[i, col] = lowest_low[i-1, col]
    return lowest_low


@njit
def relative_strength_nb(close, benmark_close, window):
    rs = np.full(close.shape, np.nan, dtype=np.float64)
    mrs = np.full(close.shape, np.nan, dtype=np.float64)
    
    # Safe division for rs_ratio: avoid division by zero
    rs_ratio = np.full(close.shape, np.nan, dtype=np.float64)
    for col in range(close.shape[1]):
        for i in range(close.shape[0]):
            if benmark_close[i] != 0 and not np.isnan(benmark_close[i]):
                rs_ratio[i, col] = close[i, col] / benmark_close[i]

    mean_rs_ratio = np.full(close.shape, np.nan, dtype=np.float64)
    for col in range(close.shape[1]):
        for i in range(window, close.shape[0]):
            window_data = rs_ratio[i-window:i, col]
            # Only calculate mean if we have valid data
            valid_count = 0
            total = 0.0
            for j in range(window):
                if not np.isnan(window_data[j]):
                    total += window_data[j]
                    valid_count += 1
            if valid_count > 0:
                mean_rs_ratio[i, col] = total / valid_count

    for col in range(close.shape[1]):
        for i in range(window, close.shape[0]):
            # Safe division for RS calculation
            if not np.isnan(rs_ratio[i, col]) and not np.isnan(rs_ratio[i-window, col]) and rs_ratio[i-window, col] != 0:
                rs[i, col] = (rs_ratio[i, col] / rs_ratio[i-window, col]) * 100 - 100
            
            # Safe division for MRS calculation
            if not np.isnan(rs_ratio[i, col]) and not np.isnan(mean_rs_ratio[i, col]) and mean_rs_ratio[i, col] != 0:
                mrs[i, col] = ((rs_ratio[i, col] / mean_rs_ratio[i, col]) - 1) * 100
            
    return rs, mrs