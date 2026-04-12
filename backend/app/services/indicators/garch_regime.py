import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm
from numba import njit
import numba as nb


def garch_filter(returns: np.ndarray, omega: float, alpha: float, beta: float) -> np.ndarray:
    """
    Compute GARCH(1,1) conditional variance given parameters.

    The GARCH(1,1) model: σ²_t = ω + α·ε²_{t-1} + β·σ²_{t-1}
    where ε_t = r_t - mean(r_t)

    Args:
        returns: 1D array of log returns
        omega: Long-run average variance component (> 0)
        alpha: Autoregressive coefficient for innovation term (>= 0)
        beta: Coefficient for lagged variance term (>= 0)

    Returns:
        1D array of conditional variances (same shape as returns)
    """
    n = len(returns)
    sigma_sq = np.full(n, np.nan)

    # Center returns
    mean_ret = np.mean(returns)
    centered_returns = returns - mean_ret

    # Initialize with long-run variance
    sigma_sq[0] = np.var(centered_returns)

    # Recursively compute conditional variance
    for t in range(1, n):
        sigma_sq[t] = omega + alpha * (centered_returns[t-1] ** 2) + beta * sigma_sq[t-1]

    return sigma_sq


def _garch_neg_log_likelihood(params: np.ndarray, returns: np.ndarray) -> float:
    """
    Negative log-likelihood of GARCH(1,1) model.

    Used as objective function for scipy.optimize.minimize.

    Args:
        params: [omega, alpha, beta]
        returns: 1D array of log returns

    Returns:
        Negative log-likelihood (scalar)
    """
    omega, alpha, beta = params

    # Validate parameters
    if omega <= 0 or alpha < 0 or beta < 0 or alpha + beta >= 1:
        return 1e10

    try:
        sigma_sq = garch_filter(returns, omega, alpha, beta)

        # Ensure no zeros or negatives in variance
        if np.any(sigma_sq <= 0):
            return 1e10

        # Log-likelihood: sum(-log(sigma_sq) - epsilon_sq / sigma_sq)
        mean_ret = np.mean(returns)
        centered_returns = returns - mean_ret

        nll = -np.sum(
            -np.log(sigma_sq) - (centered_returns ** 2) / sigma_sq
        )

        return nll if np.isfinite(nll) else 1e10

    except Exception:
        return 1e10


def fit_garch(returns: np.ndarray) -> tuple:
    """
    Estimate GARCH(1,1) parameters via Maximum Likelihood Estimation.

    Uses scipy.optimize.minimize with L-BFGS-B to find optimal parameters
    subject to: omega > 0, alpha >= 0, beta >= 0, alpha + beta < 1

    Args:
        returns: 1D array of log returns (minimum 30 observations)

    Returns:
        Tuple of (omega, alpha, beta) parameters
    """
    if len(returns) < 30:
        # Fallback for small samples
        return (1e-6, 0.1, 0.8)

    # Initial guess: simple estimates
    mean_ret = np.mean(returns)
    centered = returns - mean_ret
    var_ret = np.var(centered)

    omega_init = var_ret * 0.05
    alpha_init = 0.1
    beta_init = 0.8

    x0 = np.array([omega_init, alpha_init, beta_init])

    # Bounds for L-BFGS-B
    bounds = [
        (1e-8, None),    # omega > 0
        (0, 0.3),        # alpha in [0, 0.3]
        (0, 0.99),       # beta in [0, 0.99]
    ]

    result = minimize(
        _garch_neg_log_likelihood,
        x0,
        args=(returns,),
        method='L-BFGS-B',
        bounds=bounds,
        options={'maxiter': 1000}
    )

    if result.success:
        omega, alpha, beta = result.x
        # Ensure stationarity
        if alpha + beta >= 0.999:
            beta = 0.999 - alpha
        return (omega, alpha, beta)
    else:
        # Return initial guess if optimization fails
        return (omega_init, alpha_init, beta_init)


def garch_volatility(
    close: np.ndarray,
    window: int = 252,
    annualize_periods: int = 252
) -> np.ndarray:
    """
    Calculate rolling GARCH(1,1) annualized volatility.

    Fits GARCH parameters on a rolling window of log returns and computes
    the conditional standard deviation (annualized).

    Args:
        close: 1D array of close prices
        window: Rolling window size for parameter estimation (default: 252 trading days)
        annualize_periods: Number of periods per year for annualization (default: 252)

    Returns:
        1D array of annualized conditional volatility (same length as close)
    """
    n = len(close)
    garch_vol = np.full(n, np.nan)

    # Compute log returns
    log_returns = np.diff(np.log(close))

    # Not enough data for first window
    if n < window:
        return garch_vol

    # Rolling window: fit GARCH and compute volatility
    for i in range(window, n):
        window_returns = log_returns[i - window:i]

        # Fit GARCH on this window
        omega, alpha, beta = fit_garch(window_returns)

        # Compute conditional variance at end of window
        sigma_sq = garch_filter(window_returns, omega, alpha, beta)

        # Annualized volatility
        garch_vol[i] = np.sqrt(sigma_sq[-1]) * np.sqrt(annualize_periods)

    return garch_vol


def _hamilton_filter(
    garch_residuals: np.ndarray,
    garch_var: np.ndarray
) -> tuple:
    """
    Hamilton filter for regime detection using GARCH residuals.

    Implements a recursive Bayesian filter to compute smooth regime probabilities.
    Uses a 2-state Markov chain with Gaussian emissions.

    Args:
        garch_residuals: Centered returns (ε_t)
        garch_var: Conditional variance from GARCH (σ²_t)

    Returns:
        Tuple of (regime_prob_high, transition_matrix, regime_params)
    """
    n = len(garch_residuals)

    # Standardized residuals
    garch_std = np.sqrt(garch_var)
    std_resid = garch_residuals / np.clip(garch_std, 1e-8, None)

    # Initialize regime parameters via clustering on conditional variance
    sorted_indices = np.argsort(garch_var)
    split_point = n // 2

    # Low vol regime (regime 0) and high vol regime (regime 1)
    low_vol_indices = sorted_indices[:split_point]
    high_vol_indices = sorted_indices[split_point:]

    mu_low = np.mean(std_resid[low_vol_indices])
    mu_high = np.mean(std_resid[high_vol_indices])
    sigma_low = np.std(std_resid[low_vol_indices]) + 1e-8
    sigma_high = np.std(std_resid[high_vol_indices]) + 1e-8

    # Transition probabilities (simple estimate from regime duration)
    p_low_to_low = 0.95  # Persistence
    p_high_to_high = 0.93

    transition_matrix = np.array([
        [p_low_to_low, 1 - p_low_to_low],
        [1 - p_high_to_high, p_high_to_high]
    ])

    # Forward pass: compute regime probabilities
    regime_prob = np.full((n, 2), np.nan)
    regime_prob[0] = np.array([0.5, 0.5])  # Initial: equal probability

    for t in range(1, n):
        # Predicted probabilities from transition matrix
        pred_prob = transition_matrix.T @ regime_prob[t-1]

        # Likelihood under each regime (Gaussian)
        lik_low = norm.pdf(std_resid[t], mu_low, sigma_low)
        lik_high = norm.pdf(std_resid[t], mu_high, sigma_high)

        # Update (Bayes rule)
        lik = np.array([lik_low, lik_high])
        unnorm_prob = lik * pred_prob

        # Normalize
        if unnorm_prob.sum() > 0:
            regime_prob[t] = unnorm_prob / unnorm_prob.sum()
        else:
            regime_prob[t] = pred_prob

    return regime_prob, transition_matrix, (mu_low, mu_high, sigma_low, sigma_high)


def ms_garch_regime(
    close: np.ndarray,
    window: int = 252,
    annualize_periods: int = 252
) -> tuple:
    """
    Markov-Switching GARCH for regime detection.

    Estimates GARCH volatility and uses a Hamilton filter to identify
    low-volatility and high-volatility regimes.

    Args:
        close: 1D array of close prices
        window: Rolling window size for GARCH fitting (default: 252)
        annualize_periods: Number of periods per year for annualization (default: 252)

    Returns:
        Tuple of three arrays:
        - regime: 1D array of regime labels (0=low vol, 1=high vol)
        - regime_prob: 1D array of probability of being in high-vol regime (0..1)
        - conditional_vol: 1D array of annualized conditional volatility
    """
    n = len(close)

    # Compute log returns
    log_returns = np.diff(np.log(close))

    # Initialize output arrays
    regime = np.full(n, -1, dtype=np.int32)
    regime_prob_high = np.full(n, np.nan)
    conditional_vol = np.full(n, np.nan)

    if n < window:
        return regime, regime_prob_high, conditional_vol

    # Fit GARCH on entire returns (or use rolling window for more responsiveness)
    omega, alpha, beta = fit_garch(log_returns)

    # Compute conditional variance
    sigma_sq = garch_filter(log_returns, omega, alpha, beta)

    # Compute conditional volatility (annualized)
    conditional_vol[1:] = np.sqrt(sigma_sq) * np.sqrt(annualize_periods)

    # Apply Hamilton filter
    mean_ret = np.mean(log_returns)
    centered_returns = log_returns - mean_ret

    regime_probs, _, _ = _hamilton_filter(centered_returns, sigma_sq)

    # Extract regime probabilities and labels
    regime_prob_high[1:] = regime_probs[:, 1]  # Probability of high-vol regime
    regime[1:] = (regime_probs[:, 1] > 0.5).astype(np.int32)  # Dominant regime

    return regime, regime_prob_high, conditional_vol


@njit(parallel=True)
def garch_volatility_nb(
    returns_arr: np.ndarray,
    omega: float,
    alpha: float,
    beta: float,
    annualize_periods: float = 252.0
) -> np.ndarray:
    """
    Numba-optimized GARCH(1,1) conditional volatility for multiple symbols.

    Applies GARCH variance filter across multiple assets in parallel.
    Parameters are fixed (pre-estimated) and not re-fitted per symbol.

    Args:
        returns_arr: 2D array of log returns (rows=time, cols=symbols)
        omega: Long-run average variance component
        alpha: Autoregressive coefficient
        beta: Lagged variance coefficient
        annualize_periods: Number of periods per year (default: 252)

    Returns:
        2D array of annualized conditional volatility (same shape as returns_arr)
    """
    n_rows, n_cols = returns_arr.shape
    volatility = np.full((n_rows, n_cols), np.nan, dtype=np.float64)

    for col in nb.prange(n_cols):
        returns = returns_arr[:, col]

        # Center returns
        mean_ret = np.mean(returns)
        centered = returns - mean_ret

        # Initialize conditional variance
        sigma_sq = np.full(n_rows, np.nan)
        sigma_sq[0] = np.var(centered)

        # GARCH recursion
        for i in range(1, n_rows):
            sigma_sq[i] = omega + alpha * (centered[i-1] ** 2) + beta * sigma_sq[i-1]

        # Annualize volatility
        volatility[:, col] = np.sqrt(sigma_sq) * np.sqrt(annualize_periods)

    return volatility
