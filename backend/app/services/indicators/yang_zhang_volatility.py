import numpy as np
import pandas as pd
from typing import List

def calculate_yz_volatility(
    open_prices: np.ndarray,
    high_prices: np.ndarray,
    low_prices: np.ndarray,
    close_prices: np.ndarray,
    window: int = 30,
    periods: int = 252
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
    yz_volatility = np.sqrt(sigma_yz_sq) * np.sqrt(periods)
    
    return yz_volatility.fillna(0).tolist()

    
