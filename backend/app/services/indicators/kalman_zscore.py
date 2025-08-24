from typing import List
import numpy as np
import pandas as pd
from pykalman import KalmanFilter

def calculate_kalman_zscore(close_prices: np.array, window: int = 20) -> List[float]:
    """
    Calculate z-score using Kalman filter smoothing
    
    Args:
        close_prices: List of closing prices
        window: Rolling window size for mean and std calculation
    
    Returns:
        List of z-score values
    """
    # Initialize Kalman Filter
    kf = KalmanFilter(
        transition_matrices=[1],
        observation_matrices=[1],
        initial_state_mean=0,
        initial_state_covariance=1,
        observation_covariance=1,
        transition_covariance=0.01
    )

    # Convert input to numpy array and ensure it's float64
    prices_array = np.array(close_prices, dtype=np.float64)
    
    # Get Kalman smoothed values
    state_means, _ = kf.filter(prices_array)
    kalman_avg = state_means.flatten()
    
    # Convert to pandas series for rolling calculations
    kalman_series = pd.Series(kalman_avg)
    
    # Calculate rolling statistics
    rolling_mean = kalman_series.rolling(window=window).mean()
    rolling_std = kalman_series.rolling(window=window).std()
    
    # Calculate z-score
    z_score = (kalman_series - rolling_mean) / rolling_std
    return z_score.fillna(0).values.tolist()
