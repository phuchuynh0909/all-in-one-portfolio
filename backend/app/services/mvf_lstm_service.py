"""MVF — Mean-Variance with Forecasting (LSTM μ + shrunk historical Σ).

Ported from ``notebooks/mvf_lstm_portfolio.ipynb``, Phase 4 ("final model &
deployable weights"): train one LSTM per asset on the *entire* history,
recursively forecast the next ``horizon`` days, take μ from those forecast paths
and Σ from Ledoit-Wolf-shrunk trailing historical returns, then solve the
long-only capped max-Sharpe portfolio and turn it into an order sheet.

The notebook's earlier phases (train/test split, algorithm benchmark,
walk-forward validation) stay in the notebook — they *validate* the recipe;
this module serves the allocation you would actually put on as of the last bar.

Training is the slow part (seconds per asset), so fitted weights are cached on
disk under ``<model_path>/lstm_cache``, keyed by a fingerprint of the training
data plus every knob that changes the fit. :func:`stream_mvf` is a generator of
``(event, payload)`` pairs so the API can report per-asset progress while a run
is still in flight.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Generator, Iterator

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from loguru import logger
from scipy.optimize import minimize
from sklearn.covariance import LedoitWolf
from torch.utils.data import DataLoader, TensorDataset

from app.core.settings import settings
from app.schemas.mvf import MvfHolding, MvfRequest, MvfResult
from app.services.indicators.gkyz_volatility import gkyz_volatility_nb
from app.services.stock_service import _load_delta_stocks

ANN = 252  # trading days per year, for annualizing μ and Σ
SEED = 42
GKYZ_WINDOW = 21

# The model input is [standardised log-return, own GKYZ vol, market GKYZ vol].
FEATURE_NAMES = ("ret_z", "gkyz_sym", "gkyz_mkt")
N_FEAT = len(FEATURE_NAMES)

# Must stay in sync with PriceLSTM's defaults — it is part of the cache key, so
# a change here invalidates every cached model rather than loading a mismatch.
ARCH = {"n_feat": N_FEAT, "hidden": 32, "layers": 2, "dropout": 0.2}

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

_CACHE_DIR = Path(settings.model_path) / "lstm_cache"


# ── model ────────────────────────────────────────────────────────────────────
class PriceLSTM(nn.Module):
    """Two-layer LSTM regressing the next standardised log-return."""

    def __init__(self, n_feat: int = N_FEAT, hidden: int = 32, layers: int = 2,
                 dropout: float = 0.2) -> None:
        super().__init__()
        self.lstm = nn.LSTM(input_size=n_feat, hidden_size=hidden, num_layers=layers,
                            batch_first=True, dropout=dropout)
        self.head = nn.Linear(hidden, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # x: (B, seq_len, n_feat)
        out, _ = self.lstm(x)
        return self.head(out[:, -1, :]).squeeze(-1)      # (B,)


# ── data preparation ─────────────────────────────────────────────────────────
@dataclass
class _Prepared:
    prices: pd.DataFrame     # dates × universe, gap-free
    log_ret: pd.DataFrame    # dates × universe (one row shorter than prices)
    gkyz_sym: pd.DataFrame   # dates × universe, aligned to log_ret.index, [0,1]
    gkyz_mkt: pd.Series      # market (benchmark) volatility regime, aligned too
    universe: list[str]
    dropped: list[str]       # requested tickers without enough clean history


def _panels(symbols: list[str], years: int) -> dict[str, pd.DataFrame]:
    """Wide OHLCV panels (dates × symbols) from the ClickHouse EOD table."""
    start = pd.Timestamp.now() - pd.DateOffset(years=years)
    raw = _load_delta_stocks(
        symbols=symbols,
        start=start,
        columns=["date", "symbol", "open", "high", "low", "close", "volume"],
    )
    if raw.empty:
        raise ValueError("No OHLCV data returned for the requested symbols")

    raw = raw.drop_duplicates(subset=["date", "symbol"], keep="last")
    return {
        field: raw.pivot(index="date", columns="symbol", values=field)
                  .sort_index()
                  .astype(float)
        for field in ("open", "high", "low", "close", "volume")
    }


def _prepare(req: MvfRequest, tickers: list[str]) -> _Prepared:
    """Load prices, drop names without enough history, and build the GKYZ channels."""
    benchmark = req.benchmark.upper()
    panels = _panels(sorted(set(tickers) | {benchmark}), req.years)
    close = panels["close"]

    # The LSTM needs seq_len+1 bars to form a single training sequence; require a
    # comfortable multiple of that so a name is actually learnable, and report the
    # rejects instead of letting one short series truncate the whole panel.
    min_bars = req.seq_len + req.horizon + 60
    universe, dropped = [], []
    for t in tickers:
        if t in close.columns and int(close[t].notna().sum()) >= min_bars:
            universe.append(t)
        else:
            dropped.append(t)
    if len(universe) < 2:
        raise ValueError(
            f"Need at least 2 tickers with ≥{min_bars} bars of history; "
            f"usable: {universe or 'none'} | rejected: {dropped}"
        )

    # ffill fills mid-series holidays/halts; dropna then trims only the leading
    # warm-up where the youngest listing has no data yet.
    prices = close[universe].ffill().dropna()
    log_ret = np.log(prices).diff().dropna()

    # GKYZ (Garman-Klass-Yang-Zhang) volatility: OHLC-based, captures intraday
    # range + overnight gaps. We feed the rolling min-max normalised [0,1] value
    # for the asset itself and for the benchmark (systemic risk context). Both are
    # trailing-only, so there is no look-ahead.
    gkyz_syms = universe + ([benchmark] if benchmark in close.columns else [])

    def ohlc(field: str) -> np.ndarray:
        return (panels[field][gkyz_syms].reindex(prices.index)
                .ffill().bfill().to_numpy(np.float64))

    grid = gkyz_volatility_nb(ohlc("close"), ohlc("open"), ohlc("high"), ohlc("low"),
                              window=GKYZ_WINDOW, normalize=True)
    panel = (pd.DataFrame(grid, index=prices.index, columns=gkyz_syms)
             .reindex(log_ret.index)
             .ffill().fillna(0.0))          # fill the rolling-window warm-up NaNs

    gkyz_mkt = (panel[benchmark] if benchmark in panel.columns
                else pd.Series(0.0, index=log_ret.index))

    return _Prepared(prices=prices, log_ret=log_ret, gkyz_sym=panel[universe],
                     gkyz_mkt=gkyz_mkt, universe=universe, dropped=dropped)


def _asset_feat(prep: _Prepared, sym: str, z: pd.Series) -> np.ndarray:
    """Stack one asset's per-step features into (T, N_FEAT), aligned to ``z.index``."""
    idx = z.index
    return np.column_stack([
        z.to_numpy(np.float32),
        prep.gkyz_sym[sym].reindex(idx).to_numpy(np.float32),
        prep.gkyz_mkt.reindex(idx).to_numpy(np.float32),
    ]).astype(np.float32)


def _make_sequences(feat: np.ndarray, tgt: np.ndarray,
                    seq_len: int) -> tuple[np.ndarray, np.ndarray]:
    """Overlapping (window -> next-step target) pairs: X (n, seq_len, F), y (n,)."""
    feat = np.asarray(feat, dtype=np.float32)
    tgt = np.asarray(tgt, dtype=np.float32)
    n = len(feat) - seq_len
    if n <= 0:
        raise ValueError(f"Need more than seq_len={seq_len} bars to build sequences")
    X = np.stack([feat[i:i + seq_len] for i in range(n)]).astype(np.float32)
    y = tgt[seq_len:seq_len + n].astype(np.float32)
    return X, y


# ── training (with a per-symbol disk cache) ──────────────────────────────────
def _fingerprint(sym: str, feat: np.ndarray, tgt: np.ndarray, req: MvfRequest) -> str:
    """SHA over the training data plus every knob that changes the fitted weights.

    Any change (history, features, epochs, lr, batch, arch, seed, torch version)
    yields a new key, so a stale model is never silently reused.
    """
    h = hashlib.sha256()
    h.update(sym.encode())
    h.update(np.ascontiguousarray(feat, dtype=np.float32).tobytes())
    h.update(np.ascontiguousarray(tgt, dtype=np.float32).tobytes())
    cfg = {
        "seq_len": req.seq_len, "epochs": req.epochs, "lr": req.lr,
        "batch": req.batch_size, "seed": SEED, "feats": list(FEATURE_NAMES),
        "arch": ARCH, "torch": torch.__version__,
    }
    h.update(json.dumps(cfg, sort_keys=True, default=str).encode())
    return h.hexdigest()[:16]


def _train(feat: np.ndarray, tgt: np.ndarray, req: MvfRequest) -> PriceLSTM:
    X, y = _make_sequences(feat, tgt, req.seq_len)
    loader = DataLoader(
        TensorDataset(torch.tensor(X, device=DEVICE), torch.tensor(y, device=DEVICE)),
        batch_size=req.batch_size, shuffle=True,
    )
    model = PriceLSTM(**ARCH).to(DEVICE)
    opt = torch.optim.Adam(model.parameters(), lr=req.lr)
    loss_fn = nn.MSELoss()
    model.train()
    for _ in range(req.epochs):
        for xb, yb in loader:
            opt.zero_grad()
            loss_fn(model(xb), yb).backward()
            opt.step()
    model.eval()
    return model


def _train_cached(sym: str, feat: np.ndarray, tgt: np.ndarray,
                  req: MvfRequest) -> tuple[PriceLSTM, str]:
    path = _CACHE_DIR / f"mvf_{sym}_{_fingerprint(sym, feat, tgt, req)}.pt"
    if not req.force_retrain and path.exists():
        model = PriceLSTM(**ARCH).to(DEVICE)
        model.load_state_dict(torch.load(path, map_location=DEVICE))
        model.eval()
        return model, "cached"

    # Reseed per call so each asset's fit is reproducible regardless of cache hits.
    torch.manual_seed(SEED)
    model = _train(feat, tgt, req)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), path)
    return model, "trained"


# ── forecasting ──────────────────────────────────────────────────────────────
def _forecast_path(model: PriceLSTM, feat_hist: np.ndarray, seq_len: int, horizon: int,
                   mu: float, sd: float, last_price: float) -> np.ndarray:
    """Roll the model forward ``horizon`` days from the last ``seq_len`` window.

    Deterministic (eval mode) -> a stable point forecast. The volatility channels
    are held at their last observed value, since there is no future OHLC to
    recompute them from. Returns predicted *prices* of length ``horizon``.
    """
    window = [np.asarray(row, dtype=np.float32) for row in feat_hist[-seq_len:]]
    frozen = feat_hist[-1, 1:].astype(np.float32)
    price = float(last_price)
    out: list[float] = []

    model.eval()
    with torch.no_grad():
        for _ in range(horizon):
            x = torch.tensor(np.array(window[-seq_len:], dtype=np.float32),
                             device=DEVICE).unsqueeze(0)     # (1, seq_len, N_FEAT)
            z_next = float(model(x).item())                  # standardised return
            price *= float(np.exp(z_next * sd + mu))         # de-standardise -> price
            out.append(price)
            window.append(np.concatenate([[np.float32(z_next)], frozen]).astype(np.float32))
    return np.asarray(out, dtype=float)


# ── optimization ─────────────────────────────────────────────────────────────
def _shrunk_cov(daily: pd.DataFrame, shrink: bool) -> pd.DataFrame:
    """Annualized covariance of daily returns, Ledoit-Wolf shrunk when asked.

    Shrinkage keeps Σ well-conditioned (hence invertible) when the lookback is
    short relative to the number of assets — otherwise SLSQP blows up.
    """
    R = daily.to_numpy(dtype=float)
    cov = LedoitWolf().fit(R).covariance_ if shrink else np.cov(R, rowvar=False)
    return pd.DataFrame(cov * ANN, index=daily.columns, columns=daily.columns)


def _port_stats(w: np.ndarray, mu: np.ndarray, sigma: np.ndarray,
                rf: float) -> tuple[float, float, float]:
    ret = float(w @ mu)
    vol = float(np.sqrt(max(w @ sigma @ w, 0.0)))
    return ret, vol, ((ret - rf) / vol if vol > 0 else 0.0)


def _project(w: np.ndarray, w_max: float) -> np.ndarray:
    """Nudge SLSQP's answer onto the feasible set: 0 ≤ wᵢ ≤ w_max and Σw = 1.

    Naively renormalizing after a clip can push a capped weight back above the
    cap, so any shortfall is instead poured into the *remaining headroom* and any
    excess drained proportionally from the positive weights. A feasible point
    exists whenever w_max ≥ 1/n, so this converges well inside the iteration cap.
    """
    w = np.clip(w, 0.0, w_max)
    for _ in range(64):
        gap = 1.0 - w.sum()
        if abs(gap) < 1e-12:
            break
        if gap > 0:
            room = w_max - w
            if room.sum() <= 1e-15:
                break
            w = w + gap * room / room.sum()
        else:
            pos = w > 0
            if not pos.any():
                break
            w[pos] = w[pos] + gap * w[pos] / w[pos].sum()
        w = np.clip(w, 0.0, w_max)
    return w


def _max_sharpe(mu: pd.Series, sigma: pd.DataFrame, rf: float,
                w_max: float) -> pd.Series:
    """Long-only, fully-invested max-Sharpe (tangency) weights with a per-asset cap."""
    n = len(mu)
    mu_v, S = mu.to_numpy(float), sigma.to_numpy(float)
    w_max = max(w_max, 1.0 / n)          # a cap below 1/n makes the problem infeasible
    res = minimize(
        lambda w: -_port_stats(w, mu_v, S, rf)[2],
        np.repeat(1 / n, n),
        method="SLSQP",
        bounds=tuple((0.0, w_max) for _ in range(n)),
        constraints=({"type": "eq", "fun": lambda w: w.sum() - 1},),
        options={"maxiter": 1000, "ftol": 1e-10},
    )
    if not res.success:
        logger.warning("MVF max-Sharpe SLSQP did not converge: {}", res.message)
    return pd.Series(_project(res.x, w_max), index=mu.index)


# ── the streamed run ─────────────────────────────────────────────────────────
def stream_mvf(req: MvfRequest) -> Generator[tuple[str, dict], None, None]:
    """Run the full MVF pipeline, yielding ``(event, payload)`` progress pairs.

    Events: ``started``, ``loaded``, ``asset`` (once per trained/cached model),
    ``forecasting``, ``optimizing``, ``result``, and finally ``done``. Raises on
    failure — the caller is responsible for turning that into an ``error`` event.
    """
    tickers = list(dict.fromkeys(t.strip().upper() for t in req.tickers if t.strip()))
    yield "started", {"tickers": tickers, "device": DEVICE, "horizon": req.horizon}

    prep = _prepare(req, tickers)
    universe = prep.universe
    yield "loaded", {
        "universe": universe,
        "dropped": prep.dropped,
        "bars": len(prep.log_ret),
        "start": prep.prices.index[0].strftime("%Y-%m-%d"),
        "end": prep.prices.index[-1].strftime("%Y-%m-%d"),
    }

    # Standardise per asset over the full history — this is the production fit, so
    # every observation is in-sample by design (validation lives in the notebook).
    mu_r = prep.log_ret.mean()
    sd_r = prep.log_ret.std().replace(0, 1e-8)
    z_all = (prep.log_ret - mu_r) / sd_r

    models: dict[str, PriceLSTM] = {}
    for i, sym in enumerate(universe, start=1):
        feat = _asset_feat(prep, sym, z_all[sym])
        tgt = z_all[sym].to_numpy(np.float32)
        models[sym], source = _train_cached(sym, feat, tgt, req)
        logger.info("MVF {} LSTM -> {} ({}/{})", source, sym, i, len(universe))
        yield "asset", {"symbol": sym, "index": i, "total": len(universe),
                        "source": source}

    yield "forecasting", {"horizon": req.horizon}
    as_of = prep.prices.index[-1]
    last_price = prep.prices.iloc[-1]
    paths = pd.DataFrame({
        sym: _forecast_path(models[sym], _asset_feat(prep, sym, z_all[sym]),
                            req.seq_len, req.horizon,
                            float(mu_r[sym]), float(sd_r[sym]), float(last_price[sym]))
        for sym in universe
    })

    yield "optimizing", {"max_weight": req.max_weight}
    # μ: forward-looking — the LSTM's total predicted horizon return, annualized.
    total_ret = paths.iloc[-1] / last_price[universe] - 1.0
    mu_pred = (1 + total_ret) ** (ANN / req.horizon) - 1

    # Σ: trailing historical daily returns. A single deterministic forecast path
    # gives a degenerate covariance and carries no cross-asset correlation, so the
    # co-movement estimate stays historical (and shrunk).
    sigma = _shrunk_cov(prep.log_ret.iloc[-req.cov_lookback:][universe], req.cov_shrink)

    rf_ann = req.risk_free_rate * ANN
    w = _max_sharpe(mu_pred, sigma, rf_ann, req.max_weight)
    ret, vol, sharpe = _port_stats(w.to_numpy(float), mu_pred.to_numpy(float),
                                   sigma.to_numpy(float), rf_ann)

    asset_vol = pd.Series(np.sqrt(np.diag(sigma)), index=universe)
    held = w[w > 1e-4].sort_values(ascending=False)

    holdings: list[MvfHolding] = []
    for sym, weight in held.items():
        target = float(weight) * req.capital
        price = float(last_price[sym])
        shares = int(np.floor(target / price)) if price > 0 else 0
        holdings.append(MvfHolding(
            ticker=str(sym),
            weight=float(weight),
            pred_ann_return=float(mu_pred[sym]),
            ann_vol=float(asset_vol[sym]),
            last_price=price,
            shares=shares,
            target_value=target,
            alloc_value=shares * price,
        ))

    deployed = sum(h.alloc_value for h in holdings)
    result = MvfResult(
        as_of=as_of.strftime("%Y-%m-%d"),
        bars=len(prep.log_ret),
        universe=universe,
        dropped=prep.dropped,
        excluded=[s for s in universe if s not in held.index],
        horizon=req.horizon,
        max_weight=req.max_weight,
        predicted_return=ret,
        predicted_volatility=vol,
        predicted_sharpe=sharpe,
        weight_sum=float(w.sum()),
        capital=req.capital,
        deployed_value=deployed,
        cash_residual=req.capital - deployed,
        holdings=holdings,
    )
    yield "result", result.model_dump()
    yield "done", {}
