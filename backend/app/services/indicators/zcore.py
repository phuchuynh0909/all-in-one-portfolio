import numpy as np
from numba import njit, prange
import numba as nb

@njit
def zscore_nb(data, window):
    zscore_result = np.full(data.shape, np.nan) # Renamed to avoid conflict with function name
    for col in range(data.shape[1]):
        for i in range(window, data.shape[0]):
            # Slice the window once
            window_data = data[i-window:i, col]
            # Calculate standard deviation
            std_dev = np.std(window_data)
            # Check for zero standard deviation
            if std_dev == 0:
                # If all values in window are same, z-score is 0
                zscore_result[i, col] = 0.0
            else:
                # Calculate mean only if needed
                mean_val = np.mean(window_data)
                # Calculate z-score
                zscore_result[i, col] = (data[i, col] - mean_val) / std_dev
    return zscore_result