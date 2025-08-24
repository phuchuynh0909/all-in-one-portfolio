import numpy as np
from numba import njit, prange
import numba as nb

def avwap(close: np.array, high: np.array, low: np.array, volume: np.array, is_highest: bool = True, window: int = 200) -> np.array:
    n_rows = len(close)
    avwap_arr = np.full(n_rows, np.nan)
    anchor_indices = np.full(n_rows, False)

    for i in range(window - 1, n_rows):
        window_slice = slice(i - window + 1, i + 1)
        price_window = close[window_slice]
        if is_highest:
            highest_price_index = np.argmax(price_window)
        else:
            highest_price_index = np.argmin(price_window)

        anchor_index = i - window + 1 + highest_price_index
        anchor_indices[anchor_index] = True

        if anchor_indices[i] or not np.all(np.isnan(avwap_arr[:i])):
            typical_price = (close[anchor_index:] + high[anchor_index:] + low[anchor_index:]) / 3
            cum_vol = np.cumsum(volume[anchor_index:])
            cum_vol_price = np.cumsum(typical_price * volume[anchor_index:])
            avwap_values = cum_vol_price / cum_vol
            avwap_arr[anchor_index:anchor_index + len(avwap_values)] = avwap_values

    # Forward fill for each asset
    last_valid_index = 0
    for i in range(n_rows):
        if not np.isnan(avwap_arr[i]):
            avwap_arr[last_valid_index:i + 1] = avwap_arr[i]
            last_valid_index = i + 1
    avwap_arr[last_valid_index:] = avwap_arr[last_valid_index - 1]

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
        anchor_indices = np.full(n_rows, False)

        for i in range(window - 1, n_rows):
            window_slice = slice(i - window + 1, i + 1)
            close_window = close[window_slice]
            if is_highest:
                highest_price_index = np.argmax(close_window)
            else:
                highest_price_index = np.argmin(close_window)
            anchor_index = i - window + 1 + highest_price_index
            anchor_indices[anchor_index] = True
            
            if anchor_indices[i] or not np.all(np.isnan(avwap_arr[:i, col])):
                typical_price = (close[anchor_index:] + high[anchor_index:] + low[anchor_index:]) / 3
                cum_vol = np.cumsum(volume[anchor_index:])
                cum_vol_price = np.cumsum(typical_price * volume[anchor_index:])
                avwap_values = cum_vol_price / cum_vol
                avwap_arr[anchor_index:anchor_index + len(avwap_values), col] = avwap_values

        # Forward fill for each asset
        last_valid_index = 0
        for i in range(n_rows):
            if not np.isnan(avwap_arr[i, col]):
                avwap_arr[last_valid_index:i + 1, col] = avwap_arr[i, col]
                last_valid_index = i + 1
        avwap_arr[last_valid_index:, col] = avwap_arr[last_valid_index - 1, col]

    return avwap_arr