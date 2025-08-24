import numpy as np
from scipy.special import stdtr

def hawkes_BVC(close: np.array, volume: np.array, window: int = 20, kappa: float = 0.1) -> np.array:
    """Calculate Hawkes Bid Volume Classification indicator.
    
    Args:
        close: Array of closing prices
        volume: Array of volume values
        window: Window size for calculation (default: 20)
        kappa: Decay factor (default: 0.1)
    
    Returns:
        Array of BVC values
    """
    alpha = np.exp(-kappa)
    bvc = np.full(close.shape, np.nan, dtype=np.float64)  # Ensure bvc is of type float
    cumr = np.log(close[:] / close[0])
    r = np.diff(cumr, prepend=cumr[0])
    r[np.isnan(r)] = 0.0  # Replace NaNs with 0.0
    sum = 0
    for i in range(window, close.shape[0]):
        r_window = r[i - window:i]
        sigma = np.nan_to_num(np.std(r_window), 0.0)
        if sigma > 0.0:
            cum = stdtr(0.25, r[i]/sigma)
            label = 2 * cum - 1.0
        else:
            label = 0.0
        sum = sum * alpha + volume[i] * label
        bvc[i] = sum
    return bvc / 100000  # Scale down the values for better visualization
