import numpy as np
from numba import njit
import numba as nb


def ohlc_gmm_spread(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    use_optimal_gmm: bool = True
) -> float:
    """
    Estimate bid-ask spread using OHLC-based GMM estimator (Ardia et al., 2024).

    The estimator uses three moment conditions derived from OHLC log-returns to
    estimate the fractional bid-ask spread without requiring direct quote data.

    Based on: "Efficient Estimation of Bid-Ask Spreads from Open, High, Low, and
    Close Prices" — Ardia, D., Dufays, A., Gatarek, L.T., & Hoogerheide, L.F. (2024).

    Args:
        open_: 1D array of open prices
        high: 1D array of high prices
        low: 1D array of low prices
        close: 1D array of close prices
        use_optimal_gmm: If True, use optimal weighted GMM; if False, use equal-weight

    Returns:
        float: Estimated spread as a fraction (e.g., 0.002 = 0.2% bid-ask spread)
    """
    o = np.log(np.asarray(open_, dtype=np.float64))
    h = np.log(np.asarray(high, dtype=np.float64))
    l = np.log(np.asarray(low, dtype=np.float64))
    c = np.log(np.asarray(close, dtype=np.float64))

    n = len(o)
    if n < 2:
        return 0.0

    # Compute the six log-return components (starting from bar 1, use bar 0 as reference)
    r1 = o[1:] - c[:-1]        # overnight gap: log(O_t) - log(C_{t-1})
    r2 = c[1:] - o[1:]         # intraday: log(C_t) - log(O_t)
    r3 = h[1:] - o[1:]         # high above open: log(H_t) - log(O_t)
    r4 = l[1:] - o[1:]         # low below open: log(L_t) - log(O_t)
    r5 = h[1:] - c[1:]         # high above close: log(H_t) - log(C_t)
    r6 = l[1:] - c[1:]         # low below close: log(L_t) - log(C_t)

    # Three moment products: each identifies s²/4
    g1 = r1 * r2               # E[g1] = -s²/4
    g2 = r3 * r4               # E[g2] = -s²/4
    g3 = r5 * r6               # E[g3] = -s²/4

    if use_optimal_gmm:
        # Optimal GMM: weight by inverse of covariance matrix
        # G = [E[g1], E[g2], E[g3]]^T
        G = np.array([np.mean(g1), np.mean(g2), np.mean(g3)])

        # Omega = covariance of moments
        moments = np.column_stack([g1, g2, g3])
        Omega = np.cov(moments.T)

        # Handle singular matrix (e.g., all moments identical)
        try:
            W = np.linalg.inv(Omega)
        except np.linalg.LinAlgError:
            # Fall back to equal-weight if covariance is singular
            return float(np.sqrt(max(0.0, -4.0 / 3.0 * np.mean([np.mean(g1), np.mean(g2), np.mean(g3)]))))

        # Optimal GMM: s² = -4 * (1^T W G) / (1^T W 1)
        ones = np.ones(3)
        numerator = np.dot(ones, np.dot(W, G))
        denominator = np.dot(ones, np.dot(W, ones))

        if denominator == 0:
            return 0.0

        s2 = -4.0 * numerator / denominator
    else:
        # Equal-weight (simple) GMM: s² = -4/3 * (G1 + G2 + G3)
        s2 = -4.0 / 3.0 * (np.mean(g1) + np.mean(g2) + np.mean(g3))

    return float(np.sqrt(max(0.0, s2)))


def rolling_ohlc_gmm_spread(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    window: int = 21,
    use_optimal_gmm: bool = True
) -> np.ndarray:
    """
    Calculate rolling OHLC-based GMM bid-ask spread estimate.

    For each bar i, estimates the spread using the window [i-window+1, i].
    No look-ahead bias: only uses data available at bar i's close.

    Args:
        open_: 1D array of open prices
        high: 1D array of high prices
        low: 1D array of low prices
        close: 1D array of close prices
        window: Rolling window size in bars (default: 21 trading days)
        use_optimal_gmm: If True, use optimal weighted GMM; if False, use equal-weight

    Returns:
        1D array of spread estimates (same length as input), NaN for first window-1 bars
    """
    n = len(close)
    spread = np.full(n, np.nan)

    if n < window:
        return spread

    o = np.log(np.asarray(open_, dtype=np.float64))
    h = np.log(np.asarray(high, dtype=np.float64))
    l = np.log(np.asarray(low, dtype=np.float64))
    c = np.log(np.asarray(close, dtype=np.float64))

    # Compute all moment products at once
    r1 = o[1:] - c[:-1]
    r2 = c[1:] - o[1:]
    r3 = h[1:] - o[1:]
    r4 = l[1:] - o[1:]
    r5 = h[1:] - c[1:]
    r6 = l[1:] - c[1:]

    g1 = r1 * r2
    g2 = r3 * r4
    g3 = r5 * r6

    # Rolling window estimation
    for i in range(window - 1, n):
        # Window from bar i-window+1 to bar i (indices in g1, g2, g3 are 1-indexed relative to original)
        # Since g1 is computed from o[1:], g1[j] corresponds to bar j+1 of original prices
        # So for price bar i, we use g1[i-1], etc.
        start_idx = i - window + 1 - 1  # -1 because g arrays are shifted by 1
        end_idx = i - 1 + 1             # We include up to g1[i-1]

        if start_idx < 0:
            start_idx = 0

        g1_window = g1[start_idx:end_idx]
        g2_window = g2[start_idx:end_idx]
        g3_window = g3[start_idx:end_idx]

        if len(g1_window) < 2:
            continue

        if use_optimal_gmm:
            G = np.array([np.mean(g1_window), np.mean(g2_window), np.mean(g3_window)])

            moments = np.column_stack([g1_window, g2_window, g3_window])
            try:
                Omega = np.cov(moments.T)
                W = np.linalg.inv(Omega)
            except (np.linalg.LinAlgError, ValueError):
                # Fall back to equal-weight
                s2 = -4.0 / 3.0 * (np.mean(g1_window) + np.mean(g2_window) + np.mean(g3_window))
                spread[i] = float(np.sqrt(max(0.0, s2)))
                continue

            ones = np.ones(3)
            numerator = np.dot(ones, np.dot(W, G))
            denominator = np.dot(ones, np.dot(W, ones))

            if denominator == 0:
                continue

            s2 = -4.0 * numerator / denominator
        else:
            s2 = -4.0 / 3.0 * (np.mean(g1_window) + np.mean(g2_window) + np.mean(g3_window))

        spread[i] = float(np.sqrt(max(0.0, s2)))

    return spread


@njit(parallel=True)
def ohlc_gmm_spread_nb(
    open_arr: np.ndarray,
    high_arr: np.ndarray,
    low_arr: np.ndarray,
    close_arr: np.ndarray,
    window: int = 21
) -> np.ndarray:
    """
    Numba-optimized rolling OHLC-based GMM spread for multiple symbols.

    Uses equal-weight GMM (optimal GMM requires matrix inverse, not Numba-friendly).
    Applies spread estimation in parallel across symbols.

    Args:
        open_arr: 2D array of open prices (rows=time, cols=symbols)
        high_arr: 2D array of high prices
        low_arr: 2D array of low prices
        close_arr: 2D array of close prices
        window: Rolling window size (default: 21)

    Returns:
        2D array of spread estimates (same shape as input), NaN for first window-1 bars
    """
    n_rows, n_cols = open_arr.shape
    spread = np.full((n_rows, n_cols), np.nan, dtype=np.float64)

    for col in nb.prange(n_cols):
        o = np.log(open_arr[:, col])
        h = np.log(high_arr[:, col])
        l = np.log(low_arr[:, col])
        c = np.log(close_arr[:, col])

        # Compute moment products
        r1 = o[1:] - c[:-1]
        r2 = c[1:] - o[1:]
        r3 = h[1:] - o[1:]
        r4 = l[1:] - o[1:]
        r5 = h[1:] - c[1:]
        r6 = l[1:] - c[1:]

        g1 = r1 * r2
        g2 = r3 * r4
        g3 = r5 * r6

        # Rolling window
        for i in range(window - 1, n_rows):
            start_idx = max(0, i - window + 1 - 1)
            end_idx = i - 1 + 1

            if end_idx - start_idx < 2:
                continue

            G1_mean = 0.0
            G2_mean = 0.0
            G3_mean = 0.0

            for j in range(start_idx, end_idx):
                G1_mean += g1[j]
                G2_mean += g2[j]
                G3_mean += g3[j]

            window_len = end_idx - start_idx
            G1_mean /= window_len
            G2_mean /= window_len
            G3_mean /= window_len

            # Equal-weight GMM: s² = -4/3 * (G1 + G2 + G3)
            s2 = -4.0 / 3.0 * (G1_mean + G2_mean + G3_mean)
            spread[i, col] = np.sqrt(max(0.0, s2))

    return spread
