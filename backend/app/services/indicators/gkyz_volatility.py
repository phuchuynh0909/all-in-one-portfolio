import numpy as np
import pandas as pd
from numba import njit
import numba as nb


def calculate_gkyz_volatility(
    open_prices: np.ndarray,
    high_prices: np.ndarray,
    low_prices: np.ndarray,
    close_prices: np.ndarray,
    window: int = 21,
    normalize: bool = True,
) -> np.ndarray:
    """
    Garman-Klass-Yang-Zhang volatility estimator.

    Combines three components:
      - HL:  0.5 * mean(log(H/L)^2)
      - CC: -(2*ln2-1) * mean(log(C/C_prev)^2)
      - OC:  mean(log(O/C_prev)^2)

    Args:
        open_prices:  1-D array of open prices
        high_prices:  1-D array of high prices
        low_prices:   1-D array of low prices
        close_prices: 1-D array of close prices
        window:       lookback period (default 21)
        normalize:    min-max normalize output to [0, 1] over the same window

    Returns:
        1-D numpy array of GKYZ volatility values (NaN for first `window-1` bars)
    """
    opens  = pd.Series(open_prices, dtype=float).round(2)
    highs  = pd.Series(high_prices, dtype=float).round(2)
    lows   = pd.Series(low_prices, dtype=float).round(2)
    closes = pd.Series(close_prices, dtype=float).round(2)

    prev_close = closes.shift(1).fillna(closes)

    log_oc = np.log(opens  / prev_close)   # overnight gap (log)
    log_hl = np.log(highs  / lows)         # intraday range (log)
    log_co = np.log(closes / opens)        # drift vs TODAY'S open ✅ FIX 2

    oc_comp = (log_oc ** 2).rolling(window).mean()
    hl_comp = 0.5 * (log_hl ** 2).rolling(window).mean()
    co_comp = -(2 * np.log(2) - 1) * (log_co ** 2).rolling(window).mean()

    raw = np.sqrt((oc_comp + hl_comp + co_comp).clip(lower=0))
    
    result = raw * np.sqrt(252) * 100
    if normalize:
        lo = result.rolling(window).min()
        hi = result.rolling(window).max()
        result = (result - lo) / (hi - lo + 1e-10)

    return result.to_numpy()


@njit(parallel=True)
def gkyz_volatility_nb(
    close: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    window: int = 21,
) -> np.ndarray:
    """
    Numba-accelerated GKYZ volatility for 2-D arrays (rows=time, cols=symbols).

    Returns raw (non-normalized) GKYZ volatility; NaN for first `window-1` rows.
    """
    n_rows, n_cols = close.shape
    out = np.full(close.shape, np.nan, dtype=np.float64)

    log2 = np.log(2.0)
    cc_coeff = -(2.0 * log2 - 1.0)

    for col in nb.prange(n_cols):
        for i in range(window - 1, n_rows):
            sum_hl = 0.0
            sum_cc = 0.0
            sum_oc = 0.0

            for j in range(i - window + 1, i + 1):
                prev_j = j - 1 if j > 0 else 0
                h = high[j, col]
                l = low[j, col]
                c = close[j, col]
                o = open_[j, col]
                c_prev = close[prev_j, col]

                hl_sq = np.log(h / l) ** 2
                cc_sq = np.log(c / c_prev) ** 2
                oc_sq = np.log(o / c_prev) ** 2

                sum_hl += hl_sq
                sum_cc += cc_sq
                sum_oc += oc_sq

            hl_comp = 0.5 * sum_hl / window
            cc_comp = cc_coeff * sum_cc / window
            oc_comp = sum_oc / window

            variance = hl_comp + cc_comp + oc_comp
            if variance < 0.0:
                variance = 0.0
            out[i, col] = np.sqrt(variance)

    return out
