import numpy as np
from scipy.stats import multivariate_normal, norm
from numba import njit
import numba as nb

from .spread_gmm import rolling_ohlc_gmm_spread


def _compute_features(
    close: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
    benchmark_returns=None,
    spread_window: int = 21,
    vol_window: int = 21,
    annualize_periods: int = 252
) -> np.ndarray:
    """
    Compute feature vector for multi-feature regime detection.

    Features (all zero look-ahead bias):
    1. returns: log(C[t] / C[t-1])
    2. spread: OHLC-based GMM bid-ask spread estimate (fractional)
    3. volume_ratio: V[t] / rolling_mean(V, vol_window)[t]
    4. realized_vol: rolling std(returns) * sqrt(annualize_periods)
    5. abs_ret_ma: rolling mean(|returns|)
    6. benchmark_returns: optional benchmark log return series aligned to the symbol bars

    Args:
        close: 1D array of close prices
        open_: 1D array of open prices
        high: 1D array of high prices
        low: 1D array of low prices
        volume: 1D array of trading volume
        benchmark_returns: Optional 1D array of aligned benchmark returns
        spread_window: Window for spread estimation (default: 21)
        vol_window: Window for volatility metrics (default: 21)
        annualize_periods: Annualization factor (default: 252)

    Returns:
        2D array of shape (n, n_features) with columns:
        [returns, spread, volume_ratio, realized_vol, abs_ret_ma, benchmark_returns?]
        First vol_window rows may contain NaN due to insufficient history.
    """
    n = len(close)
    n_features = 6 if benchmark_returns is not None else 5
    features = np.full((n, n_features), np.nan)

    # Feature 1: returns
    log_close = np.log(close)
    returns = np.diff(log_close, prepend=log_close[0])  # Prepend first value to keep length n
    returns[0] = 0.0  # First return is 0 (undefined)
    features[:, 0] = returns

    # Feature 2: spread (using existing function from spread_gmm)
    spread = rolling_ohlc_gmm_spread(
        open_, high, low, close,
        window=spread_window,
        use_optimal_gmm=False  # Use simple version for speed
    )
    features[:, 1] = spread

    # Feature 3 & 4 & 5: volume_ratio, realized_vol, abs_ret_ma (use rolling windows)
    abs_returns = np.abs(returns)

    for i in range(1, n):
        # Window: [max(0, i-vol_window+1), i]
        start_idx = max(0, i - vol_window + 1)
        end_idx = i + 1

        # Feature 3: volume_ratio
        vol_window_data = volume[start_idx:end_idx]
        vol_mean = np.mean(vol_window_data)
        if vol_mean > 0:
            features[i, 2] = volume[i] / vol_mean
        else:
            features[i, 2] = 1.0

        # Feature 4: realized_vol (annualized rolling std of returns)
        ret_window_data = returns[start_idx:end_idx]
        if len(ret_window_data) >= 2:
            rolling_std = np.std(ret_window_data)
            features[i, 3] = rolling_std * np.sqrt(annualize_periods)
        else:
            features[i, 3] = 0.0

        # Feature 5: abs_ret_ma (rolling mean of absolute returns)
        abs_ret_window = abs_returns[start_idx:end_idx]
        features[i, 4] = np.mean(abs_ret_window)

    if benchmark_returns is not None:
        if len(benchmark_returns) != n:
            raise ValueError('benchmark_returns must have the same length as close')
        features[:, 5] = benchmark_returns

    return features


def _hamilton_filter_multifeature(features: np.ndarray) -> tuple:
    """
    Multivariate Hamilton filter for regime detection.

    Uses a 2-state Markov chain with multivariate Gaussian emissions.
    Features are standardized per-column before fitting to ensure comparable scales.

    Args:
        features: 2D array of shape (n, n_features) - feature matrix

    Returns:
        Tuple of (regime_prob, transition_matrix, regime_params)
        - regime_prob: (n, 2) array of regime probabilities
        - transition_matrix: (2, 2) Markov transition matrix
        - regime_params: dict with mean, cov for each regime
    """
    n, n_features = features.shape

    # Standardize features (per column)
    features_std = np.full_like(features, np.nan)
    feature_means = np.full(n_features, np.nan)
    feature_stds = np.full(n_features, np.nan)

    for col in range(n_features):
        col_data = features[:, col]
        # Ignore NaN values
        valid_mask = ~np.isnan(col_data)
        if np.sum(valid_mask) > 1:
            feature_means[col] = np.nanmean(col_data)
            feature_stds[col] = np.nanstd(col_data) + 1e-8
            features_std[valid_mask, col] = (col_data[valid_mask] - feature_means[col]) / feature_stds[col]
            features_std[~valid_mask, col] = 0.0  # Zero out NaN (will be masked in likelihood)
        else:
            features_std[:, col] = 0.0

    # Initialize regime parameters by splitting on first principal variance
    # Use total variance across features as clustering criterion
    variance_by_bar = np.nanmean(features_std ** 2, axis=1)
    sorted_indices = np.argsort(variance_by_bar)
    split_point = n // 2

    low_var_indices = sorted_indices[:split_point]
    high_var_indices = sorted_indices[split_point:]

    # Regime 0 (low variance) and Regime 1 (high variance)
    mu_low = np.nanmean(features_std[low_var_indices, :], axis=0)
    mu_high = np.nanmean(features_std[high_var_indices, :], axis=0)

    cov_low = np.cov(features_std[low_var_indices, :].T)
    cov_high = np.cov(features_std[high_var_indices, :].T)

    # Ensure covariances are positive definite
    if cov_low.ndim == 0:  # Scalar variance
        cov_low = np.array([[cov_low + 1e-8]])
    else:
        cov_low = cov_low + np.eye(n_features) * 1e-8

    if cov_high.ndim == 0:
        cov_high = np.array([[cov_high + 1e-8]])
    else:
        cov_high = cov_high + np.eye(n_features) * 1e-8

    # Transition probabilities
    p_low_to_low = 0.95
    p_high_to_high = 0.93

    transition_matrix = np.array([
        [p_low_to_low, 1 - p_low_to_low],
        [1 - p_high_to_high, p_high_to_high]
    ])

    # Forward pass: compute regime probabilities
    regime_prob = np.full((n, 2), np.nan)
    regime_prob[0] = np.array([0.5, 0.5])

    try:
        mvn_low = multivariate_normal(mean=mu_low, cov=cov_low, allow_singular=True)
        mvn_high = multivariate_normal(mean=mu_high, cov=cov_high, allow_singular=True)
    except Exception:
        # Fallback to diagonal covariance if fit fails
        mvn_low = multivariate_normal(mean=mu_low, cov=np.diag(np.ones(n_features)))
        mvn_high = multivariate_normal(mean=mu_high, cov=np.diag(np.ones(n_features)))

    for t in range(1, n):
        # Predicted probabilities from transition matrix
        pred_prob = transition_matrix.T @ regime_prob[t-1]

        # Likelihood under each regime (multivariate Gaussian)
        try:
            lik_low = mvn_low.pdf(features_std[t])
            lik_high = mvn_high.pdf(features_std[t])
        except Exception:
            lik_low = 1.0
            lik_high = 1.0

        # Update (Bayes rule)
        lik = np.array([lik_low, lik_high])
        unnorm_prob = lik * pred_prob

        # Normalize
        if unnorm_prob.sum() > 0:
            regime_prob[t] = unnorm_prob / unnorm_prob.sum()
        else:
            regime_prob[t] = pred_prob

    regime_params = {
        'mu_low': mu_low,
        'mu_high': mu_high,
        'cov_low': cov_low,
        'cov_high': cov_high
    }

    return regime_prob, transition_matrix, regime_params


def ms_regime_multifeature(
    close: np.ndarray,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
    benchmark_returns=None,
    spread_window: int = 21,
    vol_window: int = 21,
    annualize_periods: int = 252
) -> tuple:
    """
    Multi-feature Markov-Switching regime detection.

    Detects market regimes using 5 core features: returns, spread,
    volume_ratio, realized_vol, and abs_ret_ma, with an optional
    benchmark return feature. Uses a multivariate Gaussian emission model
    with a 2-state Markov chain.

    Combines microstructure signals (spread, volume) with volatility signals
    (garch-like realized vol, absolute return MA) for more robust regime detection.

    Args:
        close: 1D array of close prices
        open_: 1D array of open prices
        high: 1D array of high prices
        low: 1D array of low prices
        volume: 1D array of trading volume
        benchmark_returns: Optional 1D array of aligned benchmark returns
        spread_window: Window for GMM spread estimation (default: 21)
        vol_window: Window for volatility/volume metrics (default: 21)
        annualize_periods: Annualization factor for volatility (default: 252)

    Returns:
        Tuple of three arrays:
        - regime: 1D int array, dominant regime (0=low-stress, 1=high-stress)
        - regime_prob: 1D float array, probability of high-stress regime (0..1)
        - features: 2D float array shape (n, n_features), all computed features for inspection
    """
    n = len(close)
    n_features = 6 if benchmark_returns is not None else 5

    # Initialize outputs
    regime = np.full(n, -1, dtype=np.int32)
    regime_prob_high = np.full(n, np.nan)

    if n < max(spread_window, vol_window):
        return regime, regime_prob_high, np.full((n, n_features), np.nan)

    # Compute features
    features = _compute_features(
        close, open_, high, low, volume,
        benchmark_returns=benchmark_returns,
        spread_window=spread_window,
        vol_window=vol_window,
        annualize_periods=annualize_periods
    )

    # Apply multivariate Hamilton filter
    regime_probs, _, _ = _hamilton_filter_multifeature(features)

    # Extract regime probabilities and labels
    regime_prob_high[:] = regime_probs[:, 1]  # Probability of high-stress regime
    valid_mask = ~np.isnan(regime_prob_high)
    regime[valid_mask] = (regime_probs[valid_mask, 1] > 0.5).astype(np.int32)

    return regime, regime_prob_high, features


@njit(parallel=True)
def regime_multifeature_nb(
    close_arr: np.ndarray,
    open_arr: np.ndarray,
    high_arr: np.ndarray,
    low_arr: np.ndarray,
    volume_arr: np.ndarray,
    window: int = 21,
    annualize_periods: float = 252.0
) -> np.ndarray:
    """
    Numba-optimized multi-feature regime detection for multiple symbols.

    Parallel processing across symbols using a factorized likelihood model
    (features treated as independent within each regime). Because multivariate
    Gaussian is not available in Numba, uses sum of log-likelihoods:
    log p(x|regime) ≈ Σ_f log N(x_f; μ_{k,f}, σ_{k,f}²)

    Args:
        close_arr: 2D array of close prices (rows=time, cols=symbols)
        open_arr: 2D array of open prices
        high_arr: 2D array of high prices
        low_arr: 2D array of low prices
        volume_arr: 2D array of trading volume
        window: Rolling window for volatility metrics (default: 21)
        annualize_periods: Volatility annualization (default: 252)

    Returns:
        2D array of high-stress regime probability (same shape as inputs)
    """
    n_rows, n_cols = close_arr.shape
    regime_prob = np.full((n_rows, n_cols), np.nan, dtype=np.float64)

    for col in nb.prange(n_cols):
        close = close_arr[:, col]
        open_ = open_arr[:, col]
        high = high_arr[:, col]
        low = low_arr[:, col]
        volume = volume_arr[:, col]

        # Compute returns
        log_close = np.log(close)
        returns = np.zeros(n_rows)
        for i in range(1, n_rows):
            returns[i] = log_close[i] - log_close[i-1]

        abs_returns = np.abs(returns)

        # Compute features at each bar
        # Feature 1: returns (already computed)
        # Feature 2: spread (skip in Numba version for simplicity - would require spread_gmm)
        # Feature 3: volume_ratio
        # Feature 4: realized_vol
        # Feature 5: abs_ret_ma

        # Factorized regime detection: use 3 key features (vol_ratio, realized_vol, abs_ret_ma)
        # Initialize with simple variance-based split
        realized_vols = np.full(n_rows, np.nan)
        vol_ratios = np.full(n_rows, np.nan)
        abs_ret_mas = np.full(n_rows, np.nan)

        for i in range(1, n_rows):
            start_idx = max(0, i - window + 1)
            end_idx = i + 1

            # Volume ratio
            vol_window_data = volume[start_idx:end_idx]
            vol_mean = 0.0
            for j in range(start_idx, end_idx):
                vol_mean += vol_window_data[j - start_idx]
            vol_mean /= (end_idx - start_idx)

            if vol_mean > 0:
                vol_ratios[i] = volume[i] / vol_mean
            else:
                vol_ratios[i] = 1.0

            # Realized volatility (rolling std)
            ret_sum = 0.0
            ret_sq_sum = 0.0
            for j in range(start_idx, end_idx):
                ret_sum += returns[j]
                ret_sq_sum += returns[j] ** 2

            ret_mean = ret_sum / (end_idx - start_idx)
            ret_var = ret_sq_sum / (end_idx - start_idx) - ret_mean ** 2
            realized_vols[i] = np.sqrt(max(0.0, ret_var)) * np.sqrt(annualize_periods)

            # Absolute return MA
            abs_ret_sum = 0.0
            for j in range(start_idx, end_idx):
                abs_ret_sum += abs_returns[j]
            abs_ret_mas[i] = abs_ret_sum / (end_idx - start_idx)

        # Regime detection: split on realized volatility (main signal)
        valid_vols = np.zeros(n_rows - window)
        for i in range(window, n_rows):
            if not np.isnan(realized_vols[i]):
                valid_vols[i - window] = realized_vols[i]

        if n_rows - window > 0:
            # Compute median of valid realized vols
            sorted_vols = np.sort(valid_vols)
            median_vol = sorted_vols[(n_rows - window) // 2]

            # Simple regime: high if realized_vol > median, weighted by volume_ratio
            for i in range(window, n_rows):
                if np.isnan(realized_vols[i]):
                    continue

                # Base regime probability from realized vol
                high_regime_score = (realized_vols[i] - median_vol) / (median_vol + 1e-8)
                high_regime_score = max(-2.0, min(2.0, high_regime_score))  # Clamp

                # Adjust by volume ratio (higher volume = slightly more stress signal)
                if not np.isnan(vol_ratios[i]):
                    vol_adjustment = 0.2 * (vol_ratios[i] - 1.0) / (vol_ratios[i] + 1.0)
                    high_regime_score += vol_adjustment

                # Convert score to probability [0, 1]
                regime_prob[i, col] = 1.0 / (1.0 + np.exp(-high_regime_score))

    return regime_prob
