import numpy as np
import pandas as pd
from typing import List
from numba import njit, prange
import numba as nb

def calculate_yz_volatility(
    open_prices: np.ndarray,
    high_prices: np.ndarray,
    low_prices: np.ndarray,
    close_prices: np.ndarray,
    window: int = 30,
    periods: int = 252,
    annualize: bool = True
) -> List[float]:
    """
    Calculate Yang-Zhang volatility.
    
    Args:
        open_prices: Array of opening prices
        high_prices: Array of high prices
        low_prices: Array of low prices
        close_prices: Array of closing prices
        window: Rolling window size for volatility calculation
        periods: Number of periods in a year for annualization (default: 252 trading days)
        
    Returns:
        List of annualized Yang-Zhang volatility values
    """
    # Convert numpy arrays to pandas series for easier calculation
    opens = pd.Series(open_prices)
    highs = pd.Series(high_prices)
    lows = pd.Series(low_prices)
    closes = pd.Series(close_prices)
    
    # Calculate the k factor (constant based on window size)
    k = 0.34 / (1.34 + (window + 1) / (window - 1))
    
    # Previous day's close for overnight return
    close_prev = closes.shift(1)
    
    # Calculate log returns
    log_ret_oc = np.log(opens / close_prev)  # Overnight returns
    log_ret_co = np.log(closes / opens)      # Open-to-close returns
    log_ret_hl = np.log(highs / lows)        # High/Low range returns
    
    # Squared log returns
    sq_log_ret_oc = log_ret_oc**2
    sq_log_ret_co = log_ret_co**2
    sq_log_ret_hl = log_ret_hl**2
    
    # Calculate rolling variances
    sigma_oc_sq = sq_log_ret_oc.rolling(window=window, min_periods=window).mean()
    sigma_co_sq = sq_log_ret_co.rolling(window=window, min_periods=window).mean()
    
    # Rogers-Satchell component
    rs_daily_term = 0.5 * sq_log_ret_hl - (2 * np.log(2) - 1) * (sq_log_ret_co + sq_log_ret_oc)
    sigma_rs_sq = rs_daily_term.rolling(window=window, min_periods=window).mean()
    sigma_rs_sq = sigma_rs_sq.clip(lower=0)  # Ensure non-negative
    
    # Calculate total Yang-Zhang variance
    sigma_yz_sq = sigma_oc_sq + k * sigma_co_sq + (1 - k) * sigma_rs_sq
    sigma_yz_sq = sigma_yz_sq.clip(lower=0)  # Ensure non-negative
    
    # Calculate annualized volatility
    if annualize:
        yz_volatility = np.sqrt(sigma_yz_sq) * np.sqrt(periods)
    else:
        yz_volatility = np.sqrt(sigma_yz_sq)
    
    return yz_volatility.fillna(0).tolist()

    
@njit(parallel=True)
def yang_zhang_volatility_nb(close, open, high, low, window=30, periods = 252):
    """
    Calculate Yang-Zhang volatility
    """
    n_rows, n_cols = close.shape
    yz_volatility = np.full(close.shape, np.nan, dtype=np.float32)
    k = 0.34 / (1.34 + (window + 1) / (window - 1))
    
    for col in nb.prange(n_cols):
        
        for i in range(window, n_rows):
            # Define the start index of the current window
            start_idx = i - window + 1

            # Get the price data for the current window
            window_open = open[start_idx : i + 1, col]
            window_high = high[start_idx : i + 1, col]
            window_low = low[start_idx : i + 1, col]
            window_close = close[start_idx : i + 1, col]

            # Need the previous day's close for the overnight return calculation
            # The previous close for the first day in the window (index start_idx) is close_prices[start_idx - 1]
            # The previous closes for the subsequent days in the window (indices start_idx+1 to i) are window_close[:-1]
            # So the previous closes needed are close_prices[start_idx - 1 : i]
            prev_close_indices = np.arange(max(0, start_idx - 1), i)
            previous_close_for_overnight = close[prev_close_indices, col]
            # previous_close_for_overnight = close[start_idx - 1 : i, col]

            # Calculate log returns for the current window
            # Overnight returns: log(Open_t / Close_{t-1}) for t in the window
            log_oc_window = np.log(window_open / previous_close_for_overnight)            
            # Open-to-close returns: log(Close_t / Open_t) for t in the window
            log_co_window = np.log(window_close / window_open)
            # High/Low range: log(H_t / L_t) for t in the window
            log_hl_window = np.log(window_high / window_low)

            # Calculate variances over the current window using the mean of the squared log returns
            sigma_oc_sq_window = np.mean(log_oc_window**2)
            sigma_co_sq_window = np.mean(log_co_window**2)

            # Calculate the Rogers-Satchell-like component variance over the window
            # Using a common implementation form related to Garman-Klass,
            # incorporating log(H/L) and log(C/O)
            sigma_rs_sq_window = np.mean(0.5 * log_hl_window**2 - (2 * np.log(2) - 1) * (log_co_window**2 + log_oc_window**2))
            sigma_rs_sq_window = max(0, sigma_rs_sq_window)

            # Calculate the total Yang-Zhang variance for the current window
            sigma_yz_sq_window = sigma_oc_sq_window + k * sigma_co_sq_window + (1 - k) * sigma_rs_sq_window
            sigma_yz_sq_window = max(0, sigma_yz_sq_window)

            # Calculate the annualized volatility for the current window
            annualized_volatility_window = np.sqrt(sigma_yz_sq_window) * np.sqrt(periods)

            yz_volatility[i, col] = annualized_volatility_window

    return yz_volatility