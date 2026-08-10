"""
TICA + Gaussian HMM regime detection — pure numpy/scipy, no deeptime.

TICA  : solves the generalised eigenvalue problem C_tau @ v = lambda @ C_0 @ v
        (same maths as deeptime, zero external deps)
HMM   : Baum-Welch EM with 1-D Gaussian emissions
        (same algorithm as hmmlearn/deeptime, no compiled extensions)
"""
from __future__ import annotations

import warnings
from typing import Optional

import numpy as np
import pandas as pd
from scipy.linalg import eigh
from scipy.stats import kurtosis, skew
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

# ── tuneable defaults ─────────────────────────────────────────────────────────
_VOL_W    = 10
_TICA_LAG = 40
_N_IC     = 3
_K        = 3

_RANK_LABELS: dict[int, list[str]] = {
    2: ["Risk-On", "Risk-Off"],
    3: ["Risk-On", "Caution", "Risk-Off"],
    4: ["Risk-On", "Caution", "Risk-Off", "Crisis"],
}

_LABEL_TO_SCALE: dict[str, float] = {
    "Risk-On":  1.0,
    "Caution":  0.3,
    "Risk-Off": 0.0,
    "Crisis":   0.0,
}


# ── TICA (pure numpy) ─────────────────────────────────────────────────────────

def _tica_fit(X: np.ndarray, lag: int, dim: int) -> np.ndarray:
    """Return (n_samples, dim) IC matrix via generalised eigenvalue problem."""
    n = len(X)
    X0 = X[: n - lag]          # shape (n-lag, d)
    Xl = X[lag:]               # lagged
    C0  = X0.T @ X0 / (n - lag)          # instantaneous covariance
    Ct  = (X0.T @ Xl + Xl.T @ X0) / (2 * (n - lag))  # symmetrised lag-cov

    # Solve C_tau v = lambda C_0 v  →  sort by |eigenvalue| descending
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        vals, vecs = eigh(Ct, C0, lower=False)

    order = np.argsort(np.abs(vals))[::-1]
    W = vecs[:, order[:dim]]   # (d, dim) projection matrix
    return X @ W               # (n, dim)


# ── Gaussian HMM Baum-Welch (pure numpy) ─────────────────────────────────────

def _gauss_pdf(x: np.ndarray, mu: float, sigma: float) -> np.ndarray:
    sigma = max(sigma, 1e-6)
    return np.exp(-0.5 * ((x - mu) / sigma) ** 2) / (sigma * np.sqrt(2 * np.pi))


def _hmm_fit(
    obs: np.ndarray,
    k: int,
    means: np.ndarray,
    sigmas: np.ndarray,
    trans: np.ndarray,
    pi: np.ndarray,
    n_iter: int = 500,
    tol: float = 1e-5,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Baum-Welch EM. Returns (trans, means, sigmas, pi)."""
    T = len(obs)
    prev_ll = -np.inf

    for _ in range(n_iter):
        # ── E-step: forward-backward ──────────────────────────────────────────
        B = np.column_stack([_gauss_pdf(obs, means[j], sigmas[j]) for j in range(k)])
        B = np.maximum(B, 1e-300)

        # Forward
        alpha = np.zeros((T, k))
        alpha[0] = pi * B[0]
        scale = np.zeros(T)
        scale[0] = alpha[0].sum() or 1e-300
        alpha[0] /= scale[0]
        for t in range(1, T):
            alpha[t] = (alpha[t - 1] @ trans) * B[t]
            scale[t] = alpha[t].sum() or 1e-300
            alpha[t] /= scale[t]

        # Backward
        beta = np.ones((T, k))
        for t in range(T - 2, -1, -1):
            beta[t] = (trans * B[t + 1] * beta[t + 1]).sum(axis=1)
            beta[t] /= scale[t + 1]

        # Gamma & xi
        gamma = alpha * beta
        gamma /= gamma.sum(axis=1, keepdims=True) + 1e-300

        xi = np.zeros((T - 1, k, k))
        for t in range(T - 1):
            xi[t] = (alpha[t][:, None] * trans * B[t + 1] * beta[t + 1]) / (scale[t + 1] + 1e-300)
            xi[t] /= xi[t].sum() + 1e-300

        ll = np.sum(np.log(scale + 1e-300))
        if abs(ll - prev_ll) < tol:
            break
        prev_ll = ll

        # ── M-step ────────────────────────────────────────────────────────────
        pi    = gamma[0] / (gamma[0].sum() + 1e-300)
        xi_sum = xi.sum(axis=0)
        trans = xi_sum / (xi_sum.sum(axis=1, keepdims=True) + 1e-300)
        gsum  = gamma.sum(axis=0) + 1e-300
        means  = (gamma * obs[:, None]).sum(axis=0) / gsum
        sigmas = np.sqrt((gamma * (obs[:, None] - means) ** 2).sum(axis=0) / gsum)
        sigmas = np.maximum(sigmas, 1e-6)

    return trans, means, sigmas, pi, gamma


def _viterbi(
    obs: np.ndarray,
    trans: np.ndarray,
    means: np.ndarray,
    sigmas: np.ndarray,
    pi: np.ndarray,
) -> np.ndarray:
    """Viterbi decoding. Returns integer state sequence."""
    T, k = len(obs), len(means)
    B = np.column_stack([_gauss_pdf(obs, means[j], sigmas[j]) for j in range(k)])
    B = np.maximum(B, 1e-300)

    log_trans = np.log(trans + 1e-300)
    delta = np.full((T, k), -np.inf)
    psi   = np.zeros((T, k), dtype=int)

    delta[0] = np.log(pi + 1e-300) + np.log(B[0])
    for t in range(1, T):
        for j in range(k):
            v = delta[t - 1] + log_trans[:, j]
            psi[t, j]   = v.argmax()
            delta[t, j] = v[psi[t, j]] + np.log(B[t, j])

    path = np.zeros(T, dtype=int)
    path[-1] = delta[-1].argmax()
    for t in range(T - 2, -1, -1):
        path[t] = psi[t + 1, path[t + 1]]
    return path


# ── public API ────────────────────────────────────────────────────────────────

def tica_hmm_regime(
    close: np.ndarray,
    index: Optional[pd.DatetimeIndex] = None,
    vol_w: int = _VOL_W,
    tica_lag: int = _TICA_LAG,
    k: int = _K,
):
    """
    Fit TICA + Gaussian HMM on close prices. No deeptime/hmmlearn required.

    Returns
    -------
    regime_code   : np.ndarray int (n,)  — 0..k-1, warmup rows = 0
    regime_label  : list[str]
    position_size : np.ndarray float (n,)
    regime_meta   : dict[int, str]
    """
    n_total = len(close)
    if index is None:
        index = pd.RangeIndex(n_total)

    s_close = pd.Series(close, index=index)
    ret = s_close.pct_change().dropna()
    ann = np.sqrt(252)

    # ── features ──────────────────────────────────────────────────────────────
    rv5    = ret.rolling(max(vol_w // 2, 5)).std() * ann * 100
    rv20   = ret.rolling(vol_w).std()               * ann * 100
    rv60   = ret.rolling(vol_w * 3).std()           * ann * 100
    vol_ts = rv5 / (rv60 + 1e-8)
    vov    = rv20.rolling(vol_w).std()
    mom5   = s_close.pct_change(5)
    mom20  = s_close.pct_change(20)
    skew20 = ret.rolling(vol_w).apply(lambda x: float(skew(x)),     raw=True)
    kurt20 = ret.rolling(vol_w).apply(lambda x: float(kurtosis(x)), raw=True)

    feat = pd.DataFrame({
        "ret1d": ret, "rv5": rv5, "rv20": rv20, "rv60": rv60,
        "vol_ts": vol_ts, "vov": vov, "mom5": mom5, "mom20": mom20,
        "skew20": skew20, "kurt20": kurt20,
    }, index=ret.index).dropna()

    if len(feat) < tica_lag + k * 5:
        neutral = ["Risk-On"] * n_total
        return np.zeros(n_total, dtype=int), neutral, np.ones(n_total), {0: "Risk-On"}

    # ── TICA ──────────────────────────────────────────────────────────────────
    scaler = StandardScaler()
    X_std  = scaler.fit_transform(feat.values)
    X_ic   = _tica_fit(X_std, lag=tica_lag, dim=_N_IC)
    ic1    = X_ic[:, 0].astype(float)

    # ── initialise HMM via K-means on IC1 ─────────────────────────────────────
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        km = KMeans(n_clusters=k, random_state=42, n_init=20)
        km.fit(ic1.reshape(-1, 1))

    order  = np.argsort(km.cluster_centers_.flatten())
    means0  = km.cluster_centers_.flatten()[order]
    sigmas0 = np.array([max(ic1[km.labels_ == order[i]].std(), 1e-3) for i in range(k)])
    trans0  = np.full((k, k), 0.02 / max(k - 1, 1))
    np.fill_diagonal(trans0, 1 - 0.02)
    trans0 /= trans0.sum(axis=1, keepdims=True)
    pi0     = np.ones(k) / k

    # ── Baum-Welch ────────────────────────────────────────────────────────────
    trans_f, means_f, sigmas_f, pi_f, gamma = _hmm_fit(
        ic1, k, means0, sigmas0, trans0, pi0
    )

    labels_feat = _viterbi(ic1, trans_f, means_f, sigmas_f, pi_f)
    probs_feat  = gamma   # (T_feat, k)

    # ── regime stats + labeling ───────────────────────────────────────────────
    ret_arr = feat["ret1d"].values
    regime_stats: dict[int, dict] = {}
    for r in range(k):
        mask = labels_feat == r
        g    = ret_arr[mask]
        regime_stats[r] = {
            "ann_ret": float(g.mean() * 252 * 100) if len(g) else 0.0,
            "ann_vol": float(g.std()  * np.sqrt(252) * 100) if len(g) else 0.0,
        }

    vol_order  = sorted(regime_stats, key=lambda r: regime_stats[r]["ann_vol"])
    label_list = _RANK_LABELS.get(k, ["Risk-On", "Caution", "Risk-Off"])
    regime_meta: dict[int, str] = {r: label_list[rank] for rank, r in enumerate(vol_order)}

    # If the Caution state has strongly negative returns, re-rank it as Risk-Off
    # for r in range(k):
    #     if regime_stats[r]["ann_ret"] < -20.0 and regime_meta[r] == "Caution":
    #         regime_meta[r] = "Risk-Off"

    # ── soft position size ────────────────────────────────────────────────────
    scales     = np.array([_LABEL_TO_SCALE.get(regime_meta[r], 0.0) for r in range(k)])
    soft_size  = (probs_feat * scales).sum(axis=1)

    # ── align to original index ───────────────────────────────────────────────
    regime_code_full   = np.zeros(n_total, dtype=int)
    position_size_full = np.full(n_total, np.nan)

    if hasattr(index, "get_indexer"):
        feat_pos = index.get_indexer(feat.index)
    else:
        feat_pos = np.arange(len(feat))

    valid = feat_pos >= 0
    regime_code_full[feat_pos[valid]]   = labels_feat[valid]
    position_size_full[feat_pos[valid]] = soft_size[valid]

    regime_label_full = [regime_meta.get(int(c), "Risk-On") for c in regime_code_full]
    return regime_code_full, regime_label_full, position_size_full, regime_meta
