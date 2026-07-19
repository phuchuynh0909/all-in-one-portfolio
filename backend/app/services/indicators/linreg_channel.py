"""
Linear Regression Prediction Channels with Student's-t Confidence Bands.

Port of the MQL5 indicator described in
https://www.mql5.com/en/articles/23130 ("Linear Regression Prediction
Channels"), generalised to a 2-D (bars × symbols) rolling estimator.

For each symbol a simple OLS line ``ŷ = β₀ + β₁·x`` is fitted over the last
``window`` bars (``x = 0..window-1``).  Two Student's-t uncertainty envelopes
are evaluated at the *current* (right-edge) bar:

  Confidence interval  (mean trend uncertainty):
      ŷ(x) ± t · s · √[ 1/n + (x − x̄)² / Sxx ]

  Prediction interval  (individual observation scatter):
      ŷ(x) ± t · s · √[ 1 + 1/n + (x − x̄)² / Sxx ]

where
  s   = residual standard error = √(SSE / (n−2))
  Sxx = Σ(xᵢ − x̄)²                                (constant for fixed window)
  t   = t(α/2, n−2) critical value                (passed in, computed once)
  n   = window size

The bands are *position-dependent*: narrowest at the window centre, widening
toward the edges via the leverage term (x − x̄)²/Sxx — unlike fixed-width
Bollinger Bands.  At the right edge (x = n−1) the leverage is maximal.

Public API
----------
linreg_prediction_channels(close, window, t_crit) -> dict of DataFrames
    pandas wrapper returning reg/slope/slope_pct and both band pairs.
linreg_channel_2d(close, window, t_crit) -> tuple of np.ndarray
    numba kernel (rows=time, cols=symbols).
student_t_crit(window, confidence) -> float
    Student's-t two-sided critical value for df = window − 2.
"""

import numpy as np
import pandas as pd
from numba import njit
import numba as nb


def student_t_crit(window: int, confidence: float = 0.95) -> float:
    """Two-sided Student's-t critical value t(α/2, n−2) for ``window`` bars.

    Falls back to the normal-approximation when SciPy is unavailable.
    ``confidence`` is the central mass (0.95 → 2.5% in each tail).
    """
    df = max(window - 2, 1)
    alpha = 1.0 - confidence
    try:
        from scipy.stats import t as _t
        return float(_t.ppf(1.0 - alpha / 2.0, df))
    except Exception:  # pragma: no cover - SciPy is a hard dep in this repo
        # crude normal approximation (good for large df)
        from math import sqrt, log
        # Acklam-style inverse-normal approximation for the upper quantile
        p = 1.0 - alpha / 2.0
        # rational approximation, abs error < 4.5e-4
        a = [-3.969683028665376e+01, 2.209460984245205e+02,
             -2.759285104469687e+02, 1.383577518672690e+02,
             -3.066479806614716e+01, 2.506628277459239e+00]
        b = [-5.447609879822406e+01, 1.615858368580409e+02,
             -1.556989798598866e+02, 6.680131188771972e+01,
             -1.328068155288572e+01]
        c = [-7.784894002430293e-03, -3.223964580411365e-01,
             -2.400758277161838e+00, -2.549732539343734e+00,
             4.374664141464968e+00, 2.938163982698783e+00]
        d = [7.784695709041462e-03, 3.224671290700398e-01,
             2.445134137142996e+00, 3.754408661907416e+00]
        plow, phigh = 0.02425, 1 - 0.02425
        if p < plow:
            q = sqrt(-2 * log(p))
            z = (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
        elif p <= phigh:
            q = p - 0.5
            r = q*q
            z = (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
                (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
        else:
            q = sqrt(-2 * log(1 - p))
            z = -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                 ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
        return float(z)


@njit(parallel=True)
def linreg_channel_2d(close: np.ndarray, window: int, t_crit: float):
    """Rolling OLS prediction channel for a 2-D array (rows=time, cols=symbols).

    Returns six arrays, each shaped like ``close`` with NaN for the first
    ``window - 1`` rows:

        reg       — regression-line value at the current bar (forecast ŷ)
        slope     — per-bar slope β₁
        ci_upper  — confidence-interval upper band at the current bar
        ci_lower  — confidence-interval lower band
        pi_upper  — prediction-interval upper band at the current bar
        pi_lower  — prediction-interval lower band
    """
    n_rows, n_cols = close.shape
    reg      = np.full(close.shape, np.nan, dtype=np.float64)
    slope    = np.full(close.shape, np.nan, dtype=np.float64)
    ci_upper = np.full(close.shape, np.nan, dtype=np.float64)
    ci_lower = np.full(close.shape, np.nan, dtype=np.float64)
    pi_upper = np.full(close.shape, np.nan, dtype=np.float64)
    pi_lower = np.full(close.shape, np.nan, dtype=np.float64)

    W = window
    mean_x = (W - 1) / 2.0
    # Sxx = Σ(x − x̄)² = W·(W² − 1)/12   (constant for x = 0..W-1)
    Sxx = W * (W * W - 1.0) / 12.0
    # right-edge leverage term: (x0 − x̄)²/Sxx  with x0 = W-1, so (x0-x̄)=mean_x
    lev = (mean_x * mean_x) / Sxx
    inv_n = 1.0 / W
    df = W - 2

    for col in nb.prange(n_cols):
        for i in range(W - 1, n_rows):
            # accumulate over the window ending at bar i
            sum_y  = 0.0
            sum_xy = 0.0   # Σ (x − x̄)·y  ==  Sxy
            valid  = True
            for k in range(W):
                y = close[i - W + 1 + k, col]
                if np.isnan(y):
                    valid = False
                    break
                sum_y  += y
                sum_xy += (k - mean_x) * y
            if not valid:
                continue

            mean_y = sum_y * inv_n
            b1 = sum_xy / Sxx                 # slope
            # SSE = Σ (y − ŷ)²  with ŷ_k = mean_y + b1·(k − mean_x)
            sse = 0.0
            for k in range(W):
                y = close[i - W + 1 + k, col]
                yhat = mean_y + b1 * (k - mean_x)
                d = y - yhat
                sse += d * d

            s = np.sqrt(sse / df) if df > 0 else 0.0
            # forecast at right edge (x0 = W-1): ŷ0 = mean_y + b1·mean_x
            yhat0 = mean_y + b1 * mean_x

            ci_hw = t_crit * s * np.sqrt(inv_n + lev)
            pi_hw = t_crit * s * np.sqrt(1.0 + inv_n + lev)

            reg[i, col]      = yhat0
            slope[i, col]    = b1
            ci_upper[i, col] = yhat0 + ci_hw
            ci_lower[i, col] = yhat0 - ci_hw
            pi_upper[i, col] = yhat0 + pi_hw
            pi_lower[i, col] = yhat0 - pi_hw

    return reg, slope, ci_upper, ci_lower, pi_upper, pi_lower


def linreg_prediction_channels(
    close: pd.DataFrame,
    window: int = 50,
    confidence: float = 0.95,
    t_crit: float | None = None,
) -> dict:
    """pandas wrapper around :func:`linreg_channel_2d`.

    Parameters
    ----------
    close : DataFrame (bars × symbols)
    window : int
        Regression lookback (article default 50, ≥5).
    confidence : float
        Central confidence mass (article default 0.95). Ignored if ``t_crit``
        is supplied.
    t_crit : float, optional
        Pre-computed Student's-t critical value. Computed from ``window`` and
        ``confidence`` when omitted.

    Returns
    -------
    dict[str, DataFrame] with keys:
        'reg', 'slope', 'slope_pct', 'ci_upper', 'ci_lower',
        'pi_upper', 'pi_lower'
    where ``slope_pct = slope / reg * 100`` (per-bar slope as % of price).
    """
    if window < 5:
        raise ValueError("window must be >= 5 (need df = window - 2 > 0)")
    if t_crit is None:
        t_crit = student_t_crit(window, confidence)

    arr = close.to_numpy(dtype=np.float64)
    reg, slope, ci_u, ci_l, pi_u, pi_l = linreg_channel_2d(arr, window, float(t_crit))

    idx, cols = close.index, close.columns
    _df = lambda a: pd.DataFrame(a, index=idx, columns=cols)
    with np.errstate(divide='ignore', invalid='ignore'):
        slope_pct = np.where(reg != 0, slope / reg * 100.0, np.nan)

    return {
        'reg':       _df(reg),
        'slope':     _df(slope),
        'slope_pct': _df(slope_pct),
        'ci_upper':  _df(ci_u),
        'ci_lower':  _df(ci_l),
        'pi_upper':  _df(pi_u),
        'pi_lower':  _df(pi_l),
    }
