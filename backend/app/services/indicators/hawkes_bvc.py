import numpy as np
from scipy.special import stdtr  # Student's t CDF

def hawkes_BVC(close: np.ndarray, volume: np.ndarray, window: int = 20, kappa: float = 0.1) -> np.ndarray:
    """Calculate Hawkes Bid Volume Classification indicator."""
    # Ensure float arrays
    close = np.asarray(close, dtype=np.float64)
    volume = np.asarray(volume, dtype=np.float64)

    alpha = np.exp(-kappa)
    bvc = np.full(close.shape, np.nan, dtype=np.float64)

    # Log-returns with stable prepend
    cumr = np.log(close / close[0])
    r = np.diff(cumr, prepend=cumr[0])
    r[np.isnan(r)] = 0.0

    acc = 0.0
    n = close.shape[0]
    for i in range(window, n):
        r_window = r[i - window:i]
        sigma = np.nan_to_num(np.std(r_window), nan=0.0)  # <-- fixed
        if sigma > 0.0:
            cum = stdtr(0.25, r[i] / sigma)  # df=0.25 as in your code
            label = 2.0 * cum - 1.0
        else:
            label = 0.0
        acc = acc * alpha + volume[i] * label
        bvc[i] = acc

    return bvc / 100000.0