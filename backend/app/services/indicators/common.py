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