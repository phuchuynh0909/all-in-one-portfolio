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
      - OC:  mean(log(O/C_prev)^2)           overnight gap
      - HL:  0.5 * mean(log(H/L)^2)          intraday range
      - CO: -(2*ln2-1) * mean(log(C/O)^2)    drift vs today's open

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
    normalize: bool = False,
) -> np.ndarray:
    """
    Numba-accelerated GKYZ volatility for 2-D arrays (rows=time, cols=symbols).

    Matches :func:`calculate_gkyz_volatility` (same components, 2-dp rounding,
    annualization, and rolling min-max when ``normalize=True``).

    Parameters
    ----------
    normalize : bool
        When False, return annualized GKYZ (× √252 × 100).
        When True, also min-max normalize to [0, 1] over a rolling ``window``
        (requires ``window`` non-NaN bars in the lookback, same as pandas).

    Returns NaN for the first ``window - 1`` rows; when ``normalize=True``,
    NaN until bar ``2 * window - 2``.
    """
    n_rows, n_cols = close.shape
    raw = np.full(close.shape, np.nan, dtype=np.float64)
    out = np.full(close.shape, np.nan, dtype=np.float64)

    log2 = np.log(2.0)
    co_coeff = -(2.0 * log2 - 1.0)
    ann_factor = np.sqrt(252.0) * 100.0

    for col in nb.prange(n_cols):
        for i in range(window - 1, n_rows):
            sum_oc = 0.0
            sum_hl = 0.0
            sum_co = 0.0

            for j in range(i - window + 1, i + 1):
                prev_j = 0 if j == 0 else j - 1
                h = round(high[j, col], 2)
                l = round(low[j, col], 2)
                c = round(close[j, col], 2)
                o = round(open_[j, col], 2)
                c_prev = round(close[prev_j, col], 2)

                sum_oc += np.log(o / c_prev) ** 2
                sum_hl += np.log(h / l) ** 2
                sum_co += np.log(c / o) ** 2

            oc_comp = sum_oc / window
            hl_comp = 0.5 * sum_hl / window
            co_comp = co_coeff * sum_co / window

            variance = oc_comp + hl_comp + co_comp
            if variance < 0.0:
                variance = 0.0
            raw[i, col] = np.sqrt(variance) * ann_factor

        if normalize:
            for i in range(window - 1, n_rows):
                lo = np.inf
                hi = -np.inf
                count = 0
                for k in range(i - window + 1, i + 1):
                    v = raw[k, col]
                    if not np.isnan(v):
                        count += 1
                        if v < lo:
                            lo = v
                        if v > hi:
                            hi = v
                if count >= window:
                    out[i, col] = (raw[i, col] - lo) / (hi - lo + 1e-10)
                else:
                    out[i, col] = np.nan
        else:
            for i in range(window - 1, n_rows):
                out[i, col] = raw[i, col]

    return out
