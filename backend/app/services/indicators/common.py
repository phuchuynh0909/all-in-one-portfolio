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
    n_rows = close.shape[0]
    n_cols = close.shape[1]
    
    rs = np.full((n_rows, n_cols), np.nan, dtype=np.float64)
    mrs = np.full((n_rows, n_cols), np.nan, dtype=np.float64)
    rs_ratio = np.full((n_rows, n_cols), np.nan, dtype=np.float64)
    mean_rs_ratio = np.full((n_rows, n_cols), np.nan, dtype=np.float64)
    
    # Calculate rs_ratio with safe division
    for col in range(n_cols):
        for i in range(n_rows):
            bench_val = benmark_close[i]
            if bench_val != 0.0 and not np.isnan(bench_val):
                rs_ratio[i, col] = close[i, col] / bench_val

    # Calculate rolling mean of rs_ratio
    for col in range(n_cols):
        for i in range(window, n_rows):
            valid_count = 0
            total = 0.0
            for j in range(i - window, i):
                val = rs_ratio[j, col]
                if not np.isnan(val):
                    total += val
                    valid_count += 1
            if valid_count > 0:
                mean_rs_ratio[i, col] = total / valid_count

    # Calculate RS and MRS with safe division
    for col in range(n_cols):
        for i in range(window, n_rows):
            curr_ratio = rs_ratio[i, col]
            prev_ratio = rs_ratio[i - window, col]
            mean_ratio = mean_rs_ratio[i, col]
            
            # RS calculation
            if not np.isnan(curr_ratio) and not np.isnan(prev_ratio) and prev_ratio != 0.0:
                rs[i, col] = (curr_ratio / prev_ratio) * 100.0 - 100.0
            
            # MRS calculation
            if not np.isnan(curr_ratio) and not np.isnan(mean_ratio) and mean_ratio != 0.0:
                mrs[i, col] = ((curr_ratio / mean_ratio) - 1.0) * 100.0
            
    return rs, mrs