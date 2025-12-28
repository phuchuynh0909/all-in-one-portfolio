"""
Matrix Series Indicator - Converted from PineScript
Original: http://www.wisestocktrader.com/indicators/2739-flower-indicator

Calculates the up/down oscillator lines based on price deviation
from EMA, normalized by standard deviation.
"""
import numpy as np
from numba import njit, prange


@njit
def ema_1d(data: np.ndarray, period: int) -> np.ndarray:
    """Calculate EMA for 1D array."""
    n = len(data)
    result = np.full(n, np.nan)
    if n < period:
        return result
    
    alpha = 2.0 / (period + 1)
    
    # Initialize with SMA
    sma_sum = 0.0
    for i in range(period):
        sma_sum += data[i]
    result[period - 1] = sma_sum / period
    
    # Calculate EMA
    for i in range(period, n):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    
    return result


@njit
def rolling_std_1d(data: np.ndarray, period: int) -> np.ndarray:
    """Calculate rolling standard deviation for 1D array."""
    n = len(data)
    result = np.full(n, np.nan)
    
    for i in range(period - 1, n):
        window = data[i - period + 1:i + 1]
        mean_val = np.mean(window)
        var_sum = 0.0
        for j in range(period):
            diff = window[j] - mean_val
            var_sum += diff * diff
        result[i] = np.sqrt(var_sum / period)
    
    return result


@njit(parallel=True)
def matrix_series_nb(
    close_arr: np.ndarray,
    high_arr: np.ndarray,
    low_arr: np.ndarray,
    smoother: int = 5
):
    """
    Calculate Matrix Series up/down lines for multiple symbols.
    
    Args:
        close_arr: 2D array of close prices (rows=time, cols=symbols)
        high_arr: 2D array of high prices
        low_arr: 2D array of low prices
        smoother: Smoothing period for EMA calculations (default: 5)
    
    Returns:
        Tuple of (up_arr, down_arr)
    """
    n_rows, n_cols = close_arr.shape
    
    up_arr = np.full((n_rows, n_cols), np.nan)
    down_arr = np.full((n_rows, n_cols), np.nan)
    
    for col in prange(n_cols):
        close = close_arr[:, col]
        high = high_arr[:, col]
        low = low_arr[:, col]
        
        # ys1 = (high + low + close * 2) / 4
        ys1 = (high + low + close * 2.0) / 4.0
        
        # rk3 = ema(ys1, smoother)
        rk3 = ema_1d(ys1, smoother)
        
        # rk4 = stdev(ys1, smoother)
        rk4 = rolling_std_1d(ys1, smoother)
        
        # rk5 = (ys1 - rk3) * 200 / rk4
        rk5 = np.full(n_rows, np.nan)
        for i in range(n_rows):
            if not np.isnan(rk3[i]) and rk4[i] != 0:
                rk5[i] = (ys1[i] - rk3[i]) * 200.0 / rk4[i]
        
        # rk6 = ema(rk5, smoother)
        rk6 = ema_1d(rk5, smoother)
        
        # up = ema(rk6, smoother)
        up = ema_1d(rk6, smoother)
        
        # down = ema(up, smoother)
        down = ema_1d(up, smoother)
        
        for i in range(n_rows):
            up_arr[i, col] = up[i]
            down_arr[i, col] = down[i]
    
    return up_arr, down_arr


def matrix_series(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    smoother: int = 5
) -> tuple:
    """
    Calculate Matrix Series up/down lines for a single symbol.
    
    Args:
        close: 1D array of close prices
        high: 1D array of high prices
        low: 1D array of low prices
        smoother: Smoothing period (default: 5)
    
    Returns:
        Tuple of (up, down) arrays
    """
    close_2d = close.reshape(-1, 1)
    high_2d = high.reshape(-1, 1)
    low_2d = low.reshape(-1, 1)
    
    up, down = matrix_series_nb(close_2d, high_2d, low_2d, smoother)
    
    return up.flatten(), down.flatten()
