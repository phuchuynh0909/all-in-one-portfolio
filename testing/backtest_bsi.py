#!/usr/bin/env python3
"""
Backtest: Mean-Reversion BSI on 10-second OHLC bars  (vectorbt engine)

Pipeline:
  raw tick parquet(s) → 10s OHLC bars → BSI → Roofing Filter → Z-score
  → [TRAdj EMA regime filter] → mean-reversion signals → vectorbt Portfolio

BSI rule  (Hawkes decay)
  BSI[i] = BSI[i-1] * exp(-κ) + (buyvolume[i] - sellvolume[i])

Roofing Filter  (Ehlers)
  High-Pass  : strips drift / cycles longer than hp_period
  Super Smoother : kills noise / cycles shorter than lp_period

Z-score normalisation  (expanding window, no lookahead)
  bsi_norm[i] = (bsi_rf[i] - expanding_mean) / expanding_std

TRAdj EMA Regime Filter  (Vitali Apirine, S&C Jan 2023)
  MLTP         = 2 / (periods + 1)
  TR[i]        = max(H-L, |H-prev_C|, |L-prev_C|)
  TRAdj[i]     = (TR[i] - min_TR) / (max_TR - min_TR)   over pds bars  ∈ [0,1]
  TRAdj_EMA[i] = TRAdj_EMA[i-1] + MLTP*(1+TRAdj[i]*mltp_tradj)*(C[i]-TRAdj_EMA[i-1])
  Bull regime  : close >= TRAdj_EMA  → LONG  entries only
  Bear regime  : close <  TRAdj_EMA  → SHORT entries only

Mean-reversion signal
  ARM   when |bsi_norm| >= threshold
  ENTRY when bsi_norm reverses from its local extreme:
    peaked above +threshold → SHORT   (buy-side exhaustion)
    troughed below -threshold → LONG  (sell-side exhaustion)
  SL    price high/low of the extreme bar
  TP    two-phase: BSI crosses opposite threshold, then crosses back

Usage
  python backtest_vib_bsi.py --input "data/41I1G3000_*.parquet" --plot
  python backtest_vib_bsi.py --input data/ticks_combined.parquet \\
      --bsi-threshold 2.0 --kappa 0.2 --bar 10s --regime-filter
"""

import argparse
import logging
import math
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import vectorbt as vbt
except ImportError as e:
    raise ImportError("vectorbt is required: pip install vectorbt") from e

try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
except ImportError:
    optuna = None  # type: ignore

from transform_ohlc import resolve_inputs, load_and_merge, build_ohlc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 – Hawkes BSI  +  Roofing Filter  +  Z-score
# ─────────────────────────────────────────────────────────────────────────────

def roofing_filter(
    series: np.ndarray,
    hp_period: int = 48,
    lp_period: int = 10,
) -> np.ndarray:
    """
    John Ehlers' Roofing Filter  (Cycle Analytics for Traders, 2013)

    Stage 1 – High-Pass (2-pole): removes cycles LONGER than hp_period
      α₁ = (cos(0.707·360/HP) + sin(0.707·360/HP) − 1) / cos(0.707·360/HP)
      HP[i] = (1−α₁/2)²·(x[i]−2x[i-1]+x[i-2]) + 2(1−α₁)·HP[i-1] − (1−α₁)²·HP[i-2]

    Stage 2 – Super Smoother (2-pole Butterworth): removes cycles SHORTER than lp_period
      a1 = exp(−√2·π/LP),  b1 = 2·a1·cos(√2·180/LP)
      c2=b1, c3=−a1², c1=1−c2−c3
      SS[i] = c1·(HP[i]+HP[i-1])/2 + c2·SS[i-1] + c3·SS[i-2]
    """
    n = len(series)

    angle_hp = math.radians(0.707 * 360 / hp_period)
    alpha1   = (math.cos(angle_hp) + math.sin(angle_hp) - 1) / math.cos(angle_hp)
    k1, k2, k3 = (1 - alpha1 / 2) ** 2, 2 * (1 - alpha1), (1 - alpha1) ** 2

    hp = np.zeros(n)
    for i in range(2, n):
        hp[i] = (k1 * (series[i] - 2 * series[i-1] + series[i-2])
                 + k2 * hp[i-1] - k3 * hp[i-2])

    a1 = math.exp(-math.sqrt(2) * math.pi / lp_period)
    b1 = 2 * a1 * math.cos(math.radians(math.sqrt(2) * 180 / lp_period))
    c2, c3 = b1, -(a1 ** 2)
    c1 = 1 - c2 - c3

    ss = np.zeros(n)
    for i in range(2, n):
        ss[i] = c1 * (hp[i] + hp[i-1]) / 2 + c2 * ss[i-1] + c3 * ss[i-2]

    return ss


def compute_bsi(bars: pd.DataFrame, kappa: float,
                hp_period: int = 48, lp_period: int = 10,
                min_periods: int = 30) -> pd.DataFrame:
    """
    1. BSI[i]      = BSI[i-1]·exp(-κ) + (buyvolume[i] − sellvolume[i])
    2. bsi_rf[i]   = roofing_filter(BSI)   — detrended + smoothed
    3. bsi_norm[i] = expanding Z-score of bsi_rf  (NaN during warmup)

    Adds columns: bsi, bsi_rf, bsi_norm
    """
    decay = np.exp(-kappa)
    dv    = (bars["buyvolume"].fillna(0) - bars["sellvolume"].fillna(0)).to_numpy(float)
    bsi   = np.empty_like(dv)
    val   = 0.0
    for i in range(len(dv)):
        val = val * decay + dv[i]
        bsi[i] = val

    bsi_rf = roofing_filter(bsi, hp_period=hp_period, lp_period=lp_period)

    s        = pd.Series(bsi_rf)
    exp_mean = s.expanding(min_periods=min_periods).mean()
    exp_std  = s.expanding(min_periods=min_periods).std()
    bsi_norm = ((s - exp_mean) / exp_std).to_numpy()

    bars = bars.copy()
    bars["bsi"]      = bsi
    bars["bsi_rf"]   = bsi_rf
    bars["bsi_norm"] = bsi_norm
    return bars


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 – TRAdj EMA Regime Filter  (Vitali Apirine, S&C Jan 2023)
# ─────────────────────────────────────────────────────────────────────────────

def compute_regime(
    bars: pd.DataFrame,
    periods: int     = 200,
    pds: int         = 200,
    mltp_tradj: float = 5.0,
) -> pd.DataFrame:
    """
    True Range Adjusted Exponential Moving Average regime filter.

    Parameters
    ----------
    periods     : EMA / TRAdj-EMA period
    pds         : lookback window for rolling min/max of True Range
    mltp_tradj  : volatility scaling factor (typically 5–10)

    Adds columns
    ------------
    tr          : True Range
    tradj_ema   : TRAdj EMA (volatility-adjusted EMA)
    ema         : plain EMA(close, periods)  – regime reference line
    regime      : +1 (bull, close >= TRAdj_EMA) / -1 (bear, close < TRAdj_EMA)
    """
    closes = bars["close"].to_numpy(float)
    highs  = bars["high"].to_numpy(float)
    lows   = bars["low"].to_numpy(float)
    n      = len(bars)

    # True Range
    tr = np.empty(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )

    # Rolling min/max TR over pds bars (no lookahead)
    tr_series  = pd.Series(tr)
    tr_min     = tr_series.rolling(pds, min_periods=1).min().to_numpy()
    tr_max     = tr_series.rolling(pds, min_periods=1).max().to_numpy()

    # TRAdj ∈ [0, 1]
    denom  = tr_max - tr_min
    tradj  = np.where(denom > 0, (tr - tr_min) / denom, 0.0)

    # EMA multiplier
    mltp   = 2.0 / (periods + 1)

    # TRAdj EMA (initialise at first close)
    tradj_ema = np.empty(n)
    tradj_ema[0] = closes[0]
    for i in range(1, n):
        adj_mltp     = mltp * (1.0 + tradj[i] * mltp_tradj)
        tradj_ema[i] = tradj_ema[i-1] + adj_mltp * (closes[i] - tradj_ema[i-1])

    # Plain EMA for regime comparison
    ema = np.empty(n)
    ema[0] = closes[0]
    for i in range(1, n):
        ema[i] = ema[i-1] + mltp * (closes[i] - ema[i-1])

    # Regime: price vs TRAdj EMA
    regime = np.where(closes >= tradj_ema, 1, -1).astype(np.int8)

    bars = bars.copy()
    bars["tr"]        = tr
    bars["tradj_ema"] = tradj_ema
    bars["ema"]       = ema
    bars["regime"]    = regime
    return bars


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 – Signal generation  (state machine → vectorbt arrays)
# ─────────────────────────────────────────────────────────────────────────────

def generate_signals(
    bars: pd.DataFrame,
    bsi_threshold: float = 2.0,
    allow_short: bool = True,
    use_regime: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Run the mean-reversion state machine and return vectorbt-compatible arrays.

    Parameters
    ----------
    use_regime  : if True, reads bars["regime"] (+1 bull / -1 bear) and restricts
                  entries: bull regime → LONG only, bear regime → SHORT only.

    Returns
    -------
    long_entries  : bool[n]  – open long  at open[i]
    short_entries : bool[n]  – open short at open[i]
    long_exits    : bool[n]  – exit long  at open[i]  (TP signal)
    short_exits   : bool[n]  – exit short at open[i]  (TP signal)
    sl_stop       : float[n] – SL as % of entry price  (NaN where no entry)

    SL execution is delegated to vectorbt (intra-bar fill at exact stop price).
    TP execution fires at open[i+1] via pre-computed exit signals.
    State machine mirrors vectorbt's SL logic to stay in sync.
    """
    opens    = bars["open"].to_numpy(float)
    highs    = bars["high"].to_numpy(float)
    lows     = bars["low"].to_numpy(float)
    bsi_norm = bars["bsi_norm"].to_numpy(float)
    regime   = bars["regime"].to_numpy(np.int8) if use_regime else None
    n        = len(bars)

    long_entries  = np.zeros(n, bool)
    short_entries = np.zeros(n, bool)
    long_exits    = np.zeros(n, bool)
    short_exits   = np.zeros(n, bool)
    sl_stop       = np.full(n, np.nan)

    # ── state ─────────────────────────────────────────────────────────────
    pos         = 0       # +1 long / -1 short / 0 flat
    entry_bar   = -1
    stop_loss   = 0.0
    tp_armed    = False
    armed       = False
    extreme_bsi = 0.0
    extreme_bar = 0

    for i in range(n - 1):
        b = bsi_norm[i]
        if np.isnan(b):
            continue

        # ── IN POSITION ───────────────────────────────────────────────────
        if pos != 0:
            if i == entry_bar:          # skip checks on entry bar itself
                continue

            # Mirror vectorbt's SL logic to keep state in sync
            if pos == +1 and lows[i] <= stop_loss:
                pos = 0; tp_armed = False; armed = False; extreme_bsi = 0.0
                continue
            if pos == -1 and highs[i] >= stop_loss:
                pos = 0; tp_armed = False; armed = False; extreme_bsi = 0.0
                continue

            # Two-phase TP
            if not tp_armed:
                if pos == -1 and b <= -bsi_threshold:
                    tp_armed = True
                elif pos == +1 and b >= +bsi_threshold:
                    tp_armed = True
            else:
                if pos == -1 and b > -bsi_threshold:
                    short_exits[i + 1] = True
                    pos = 0; tp_armed = False
                    continue
                if pos == +1 and b < +bsi_threshold:
                    long_exits[i + 1] = True
                    pos = 0; tp_armed = False
                    continue
            continue

        # ── FLAT ──────────────────────────────────────────────────────────
        if not armed:
            if abs(b) >= bsi_threshold:
                armed = True; extreme_bsi = b; extreme_bar = i
        else:
            if b * extreme_bsi < 0:          # BSI crossed zero → reset
                armed = False; extreme_bsi = 0.0
                if abs(b) >= bsi_threshold:
                    armed = True; extreme_bsi = b; extreme_bar = i
                continue

            if abs(b) > abs(extreme_bsi):    # still extending → update extreme
                extreme_bsi = b; extreme_bar = i
                continue

            # BSI pulled back from extreme → ENTRY
            j  = i + 1
            ep = opens[j]

            # Regime gate: bull → long only, bear → short only
            r = int(regime[i]) if regime is not None else 0
            short_allowed = allow_short and (regime is None or r == -1)
            long_allowed  =                 regime is None or r == +1

            if extreme_bsi > 0 and short_allowed:
                sl = highs[extreme_bar]
                if sl <= ep:                  # invalid SL: skip
                    armed = False; extreme_bsi = 0.0
                    continue
                short_entries[j] = True
                sl_stop[j]       = (sl - ep) / ep
                pos = -1; entry_bar = j; stop_loss = sl
                tp_armed = False; armed = False; extreme_bsi = 0.0

            elif extreme_bsi < 0 and long_allowed:
                sl = lows[extreme_bar]
                if sl >= ep:                  # invalid SL: skip
                    armed = False; extreme_bsi = 0.0
                    continue
                long_entries[j] = True
                sl_stop[j]      = (ep - sl) / ep
                pos = +1; entry_bar = j; stop_loss = sl
                tp_armed = False; armed = False; extreme_bsi = 0.0

            else:
                armed = False; extreme_bsi = 0.0

    return long_entries, short_entries, long_exits, short_exits, sl_stop


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 – vectorbt Portfolio
# ─────────────────────────────────────────────────────────────────────────────

def build_portfolio(
    bars: pd.DataFrame,
    bsi_threshold: float = 2.0,
    allow_short: bool = True,
    use_regime: bool = False,
    init_cash: float = 1_000_000.0,
    size: float = 1.0,
    fees: float = 0.0,
    freq: str = "10s",
) -> "vbt.Portfolio":
    """
    Build a vectorbt Portfolio from pre-computed mean-reversion signals.

    SL is handled by vectorbt intra-bar (fills at exact stop price).
    TP is a pre-computed exit signal (fills at next bar open).
    """
    long_e, short_e, long_x, short_x, sl_pct = generate_signals(
        bars, bsi_threshold=bsi_threshold, allow_short=allow_short,
        use_regime=use_regime,
    )

    idx = pd.DatetimeIndex(bars["stamp"])

    # Enum values: StopEntryPrice.FillPrice=2 (ref=actual fill price)
    #              StopExitPrice.StopMarket=1  (exit at exact stop price)
    #              OppositeEntryMode.Ignore=0  (state machine prevents re-entry)
    pf = vbt.Portfolio.from_signals(
        close         = pd.Series(bars["close"].values, index=idx),
        open          = pd.Series(bars["open"].values,  index=idx),
        high          = pd.Series(bars["high"].values,  index=idx),
        low           = pd.Series(bars["low"].values,   index=idx),
        entries       = pd.Series(long_e,  index=idx),
        short_entries = pd.Series(short_e, index=idx),
        exits         = pd.Series(long_x,  index=idx),
        short_exits   = pd.Series(short_x, index=idx),
        # SL as % of entry price; vectorbt exits intra-bar at exact stop price
        sl_stop            = pd.Series(sl_pct, index=idx),
        stop_entry_price   = 2,   # FillPrice – reference = actual fill (open)
        stop_exit_price    = 1,   # StopMarket – exit at exact stop price
        upon_opposite_entry= 0,   # Ignore – state machine prevents re-entry
        init_cash  = init_cash,
        size       = size,
        size_type  = "amount",
        fees       = fees,
        freq       = freq,
    )
    return pf


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 – BSI panel plot  (matplotlib, separate from vectorbt's portfolio plot)
# ─────────────────────────────────────────────────────────────────────────────

def plot_bsi_panel(bars: pd.DataFrame, pf: "vbt.Portfolio",
                   bsi_threshold: float = 2.0, title: str = "",
                   use_regime: bool = False):
    """
    Three-panel matplotlib chart:
      1. Price + trade entry/exit markers
      2. BSI Z-score with ±threshold bands
      3. Cumulative P&L (from vectorbt trade records)
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        log.warning("matplotlib not installed – skipping BSI panel plot")
        return None

    bar_idx  = np.arange(len(bars))
    closes   = bars["close"].to_numpy()
    bsi_vals = bars["bsi_norm"].to_numpy()
    stamps   = bars["stamp"].to_numpy()
    stamp_to_idx = {s: i for i, s in enumerate(stamps)}

    # Pull trades from vectorbt
    trades_df = pf.trades.records_readable
    if not trades_df.empty:
        # vectorbt uses column names: 'Entry Timestamp', 'Exit Timestamp', etc.
        trades_df = trades_df.copy()

    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(14, 10), sharex=True,
        gridspec_kw={"height_ratios": [2, 1, 1]}
    )
    fig.suptitle(title or "OHLC-BSI Mean-Reversion (vectorbt)",
                 fontsize=13, fontweight="bold")

    # ── Panel 1: Price + trade markers ────────────────────────────────────
    ax1.plot(bar_idx, closes, color="steelblue", linewidth=0.8, label="close")

    if not trades_df.empty:
        for _, t in trades_df.iterrows():
            ei = stamp_to_idx.get(t.get("Entry Timestamp"))
            xi = stamp_to_idx.get(t.get("Exit Timestamp"))
            if ei is None or xi is None:
                continue
            is_long = str(t.get("Direction", "")).lower() == "long"
            color   = "green" if is_long else "red"
            ax1.axvspan(ei, xi, alpha=0.08, color=color)
            ax1.scatter(ei, t.get("Avg Entry Price", closes[ei]),
                        marker="^" if is_long else "v",
                        color=color, s=60, zorder=5)
            exit_reason = str(t.get("Exit Type", "")).lower()
            is_tp = "target" in exit_reason or "tp" in exit_reason
            pnl_val = None
            for _col in ("PnL", "P&L", "Return"):
                v = t.get(_col)
                if v is not None and not (isinstance(v, float) and np.isnan(v)):
                    try:
                        pnl_val = float(v)
                        break
                    except (TypeError, ValueError):
                        continue
            is_profit = pnl_val is not None and pnl_val > 0
            if is_tp:
                exit_color = "gold"
                exit_marker = "*"
            else:
                exit_marker = "x"
                # Non-TP exit: highlight profitable scratch / SL+ fees wins vs loss
                exit_color = "mediumseagreen" if is_profit else "crimson"
            ax1.scatter(
                xi,
                t.get("Avg Exit Price", closes[xi]),
                marker=exit_marker,
                color=exit_color,
                s=70,
                zorder=5,
            )

    # Regime shading + TRAdj EMA / EMA lines
    if use_regime and "regime" in bars.columns:
        regime_vals = bars["regime"].to_numpy()
        # Shade bull/bear regions
        in_bull = None
        for k in range(len(regime_vals)):
            if regime_vals[k] == 1 and in_bull is None:
                in_bull = k
            elif regime_vals[k] == -1 and in_bull is not None:
                ax1.axvspan(in_bull, k, alpha=0.06, color="green", zorder=0)
                in_bull = None
        if in_bull is not None:
            ax1.axvspan(in_bull, len(regime_vals) - 1, alpha=0.06, color="green", zorder=0)
        # TRAdj EMA and EMA lines
        if "tradj_ema" in bars.columns:
            ax1.plot(bar_idx, bars["tradj_ema"].to_numpy(),
                     color="orange", linewidth=0.9, linestyle="-", label="TRAdj EMA", zorder=2)
        if "ema" in bars.columns:
            ax1.plot(bar_idx, bars["ema"].to_numpy(),
                     color="purple", linewidth=0.9, linestyle="--", label="EMA", zorder=2)

    ax1.set_ylabel("Price")
    ax1.legend(loc="upper left", fontsize=8)

    # ── Panel 2: BSI Z-score + threshold bands ────────────────────────────
    ax2.plot(bar_idx, bsi_vals, color="#10a4f4", linewidth=0.8, label="BSI norm")
    ax2.axhline(0,              color="gray",          linewidth=0.5, linestyle="--")
    ax2.axhline(+bsi_threshold, color="salmon",        linewidth=0.9, linestyle="--",
                label=f"+{bsi_threshold:.1f}σ")
    ax2.axhline(-bsi_threshold, color="mediumseagreen",linewidth=0.9, linestyle="--",
                label=f"-{bsi_threshold:.1f}σ")
    ax2.fill_between(bar_idx, bsi_vals, +bsi_threshold,
                     where=(bsi_vals >= bsi_threshold), alpha=0.12, color="salmon")
    ax2.fill_between(bar_idx, bsi_vals, -bsi_threshold,
                     where=(bsi_vals <= -bsi_threshold), alpha=0.12, color="mediumseagreen")
    ax2.set_ylim(-5, 5)
    ax2.set_ylabel("BSI (Z-score)")
    ax2.legend(loc="upper left", fontsize=7)

    # ── Panel 3: Cumulative P&L from vectorbt ─────────────────────────────
    cum_returns = pf.cumulative_returns()
    ax3.plot(np.arange(len(cum_returns)), cum_returns.values * 100,
             color="darkorange", linewidth=1.2)
    ax3.fill_between(np.arange(len(cum_returns)), cum_returns.values * 100, 0,
                     where=(cum_returns.values >= 0), alpha=0.12, color="green")
    ax3.fill_between(np.arange(len(cum_returns)), cum_returns.values * 100, 0,
                     where=(cum_returns.values < 0),  alpha=0.12, color="red")
    ax3.axhline(0, color="gray", linewidth=0.5, linestyle="--")
    ax3.set_ylabel("Cum Return (%)")
    ax3.set_xlabel("OHLC bar index")

    plt.tight_layout()
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 – Optuna hyper-parameter search
# ─────────────────────────────────────────────────────────────────────────────

def run_optuna(
    base_bars: pd.DataFrame,          # raw OHLC (no BSI yet)
    n_trials: int = 100,
    bsi_threshold: float = 2.0,
    allow_short: bool = True,
    init_cash: float = 100000.0,
    size: float = 20000.0,
    fees: float = 15.0,
    freq: str = "300s",
    min_trades: int = 10,             # prune if too few trades (unstable Sharpe)
    n_jobs: int = 1,
    study_name: str = "bsi_roofing",
    storage: str | None = None,       # e.g. "sqlite:///optuna.db" for persistence
) -> "optuna.Study":
    """
    Search over (kappa, hp_period, lp_period) to maximise Sharpe Ratio.

    Parameter ranges
    ─────────────────
    kappa      : [0.1, 0.5]    float       with step 0.1   (Hawkes decay)
    hp_period  : [15,  50]    int          (Roofing high-pass cutoff)
    lp_period  : [2,   14]    int          (Roofing super-smoother cutoff)
                 constraint: lp_period < hp_period

    Objective
    ─────────
    Maximise Total Return [%] from vectorbt pf.stats()["Total Return [%]"].
    Returns -inf if fewer than min_trades closed trades (avoids overfitting
    on a handful of lucky trades).
    """
    if optuna is None:
        raise ImportError("optuna is required: pip install optuna")

    def objective(trial: "optuna.Trial") -> float:
        kappa     = trial.suggest_float("kappa",     0.1, 0.5, step=0.1)
        hp_period = trial.suggest_int(  "hp_period", 15,   50)
        lp_period = trial.suggest_int(  "lp_period", 2,    14)

        # Hard constraint: super-smoother cutoff must be < high-pass cutoff
        if lp_period >= hp_period:
            raise optuna.exceptions.TrialPruned()

        try:
            bars = compute_bsi(base_bars, kappa=kappa,
                               hp_period=hp_period, lp_period=lp_period)
            pf = build_portfolio(
                bars,
                bsi_threshold = bsi_threshold,
                allow_short   = allow_short,
                init_cash     = init_cash,
                size          = size,
                fees          = fees,
                freq          = freq,
            )
        except Exception:
            raise optuna.exceptions.TrialPruned()

        stats = pf.stats()
        n_closed = int(stats.get("Total Closed Trades", 0))
        if n_closed < min_trades:
            raise optuna.exceptions.TrialPruned()

        total_return = float(stats.get("Total Return [%]", float("-inf")))
        if not np.isfinite(total_return):
            raise optuna.exceptions.TrialPruned()
        return total_return

    sampler = optuna.samplers.TPESampler(seed=42)
    study = optuna.create_study(
        direction  = "maximize",
        sampler    = sampler,
        study_name = study_name,
        storage    = storage,
        load_if_exists = storage is not None,
    )

    log.info("Starting Optuna search: %d trials, n_jobs=%d …", n_trials, n_jobs)
    study.optimize(objective, n_trials=n_trials, n_jobs=n_jobs,
                   show_progress_bar=True)

    return study


def print_optuna_results(study: "optuna.Study") -> None:
    """Pretty-print top-5 trials and best params."""
    trials_df = study.trials_dataframe(attrs=("number", "value", "params", "state"))
    completed  = trials_df[trials_df["state"] == "COMPLETE"].copy()
    completed.sort_values("value", ascending=False, inplace=True)

    print("\n" + "=" * 60)
    print("  OPTUNA RESULTS  –  objective: Total Return [%]")
    print("=" * 60)

    print(f"\n{'Rank':<5} {'Trial':>6}  {'Return%':>9}  "
          f"{'kappa':>8}  {'hp':>5}  {'lp':>5}")
    print("-" * 48)
    for rank, (_, row) in enumerate(completed.head(5).iterrows(), 1):
        print(f"{rank:<5} {int(row['number']):>6}  {row['value']:>9.4f}  "
              f"{row['params_kappa']:>8.4f}  "
              f"{int(row['params_hp_period']):>5}  "
              f"{int(row['params_lp_period']):>5}")

    bp = study.best_params
    bv = study.best_value
    print(f"\nBest Return : {bv:.4f}%")
    print(f"  kappa     = {bp['kappa']:.4f}")
    print(f"  hp_period = {bp['hp_period']}")
    print(f"  lp_period = {bp['lp_period']}")
    print("=" * 60)

    # CLI hint so user can immediately run with best params
    print(f"\nRun best params:")
    print(f"  python backtest_vib_bsi.py "
          f"--kappa {bp['kappa']:.4f} "
          f"--hp-period {bp['hp_period']} "
          f"--lp-period {bp['lp_period']} "
          f"--bsi-threshold {{threshold}} --plot\n")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="OHLC-BSI Mean-Reversion Backtest  (vectorbt)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples
  python backtest_vib_bsi.py --input data/ticks_combined.parquet
  python backtest_vib_bsi.py --input "data/41I1G3000_*.parquet" \\
      --bsi-threshold 2.0 --kappa 0.2 --bar 300s --plot
        """,
    )
    parser.add_argument("--input",         default="data/ticks_combined.parquet")
    parser.add_argument("--bar",           default="60s",
                        help="OHLC bar size  (default: 60s)")
    parser.add_argument("--kappa",         type=float, default=0.1,
                        help="BSI Hawkes decay κ  (default 0.1)")
    parser.add_argument("--hp-period",     type=int,   default=20,
                        help="Roofing HP cutoff bars  (default 48)")
    parser.add_argument("--lp-period",     type=int,   default=5,
                        help="Roofing SS cutoff bars  (default 10)")
    parser.add_argument("--bsi-threshold", type=float, default=3.0,
                        help="Arm level in σ units  (default 2.0)")
    parser.add_argument("--init-cash",     type=float, default=10000000,
                        help="Starting capital  (default 1 000 000)")
    parser.add_argument("--size",          type=float, default=1,
                        help="Position size in units  (default 1.0)")
    parser.add_argument("--fees",          type=float, default=0,
                        help="Fee per trade as fraction  (default 0)")
    parser.add_argument("--no-short",      action="store_true",
                        help="Disable short trades")
    # ── TRAdj EMA regime filter ────────────────────────────────────────────
    parser.add_argument("--regime-filter", action="store_true",
                        help="Enable TRAdj EMA regime filter (bull→long only, bear→short only)")
    parser.add_argument("--regime-periods",type=int,   default=200,
                        help="TRAdj EMA / EMA period (default 200)")
    parser.add_argument("--regime-pds",   type=int,   default=200,
                        help="Lookback for TR min/max (default 200)")
    parser.add_argument("--regime-mltp",  type=float, default=5.0,
                        help="TRAdj volatility scaling factor (default 5.0)")
    parser.add_argument("--save-bars",     default=None,
                        help="Save OHLC+BSI bars to parquet")
    parser.add_argument("--output",        default=None,
                        help="Save trade log to CSV")
    parser.add_argument("--plot",          action="store_true",
                        help="Show BSI panel (matplotlib) + portfolio chart (vectorbt)")
    # ── Optuna hyper-parameter search ─────────────────────────────────────
    parser.add_argument("--optimize",      action="store_true",
                        help="Run Optuna search over kappa / hp-period / lp-period")
    parser.add_argument("--n-trials",      type=int,   default=100,
                        help="Number of Optuna trials  (default 100)")
    parser.add_argument("--n-jobs",        type=int,   default=1,
                        help="Parallel Optuna workers  (default 1)")
    parser.add_argument("--min-trades",    type=int,   default=10,
                        help="Min closed trades to accept a trial  (default 10)")
    parser.add_argument("--study-storage", default=None,
                        help="Optuna storage URL, e.g. sqlite:///optuna.db  "
                             "(enables persistence / resuming)")
    args = parser.parse_args()

    # ── Load ticks ────────────────────────────────────────────────────────
    input_files = resolve_inputs(args.input)
    log.info("Found %d tick file(s):", len(input_files))
    for f in input_files:
        log.info("  %s", f)
    ticks = load_and_merge(input_files)

    # ── OHLC bars ─────────────────────────────────────────────────────────
    log.info("Building %s OHLC bars …", args.bar)
    bars = build_ohlc(ticks, bar_size=args.bar)
    log.info("  %d bars produced", len(bars))

    # ── Optuna search (optional, runs before single backtest) ─────────────
    if args.optimize:
        study = run_optuna(
            base_bars     = bars,
            n_trials      = args.n_trials,
            bsi_threshold = args.bsi_threshold,
            allow_short   = not args.no_short,
            init_cash     = args.init_cash,
            size          = args.size,
            fees          = args.fees,
            freq          = args.bar,
            min_trades    = args.min_trades,
            n_jobs        = args.n_jobs,
            storage       = args.study_storage,
        )
        print_optuna_results(study)

        # Try to plot Optuna visualisations if matplotlib available
        # try:
        #     import matplotlib.pyplot as plt
        #     fig1 = optuna.visualization.matplotlib.plot_optimization_history(study)
        #     fig1.figure.suptitle("Optuna – Optimisation History (Total Return %)")
        #     fig2 = optuna.visualization.matplotlib.plot_param_importances(study)
        #     fig2.figure.suptitle("Optuna – Parameter Importances")
        #     fig3 = optuna.visualization.matplotlib.plot_contour(
        #         study, params=["kappa", "hp_period"])
        #     fig3.figure.suptitle("Optuna – Contour: kappa vs hp_period")
        #     plt.show()
        # except Exception as exc:
        #     log.debug("Optuna matplotlib visualisation skipped: %s", exc)

        # Overwrite args with best params so the single-run below uses them
        bp = study.best_params
        args.kappa     = bp["kappa"]
        args.hp_period = bp["hp_period"]
        args.lp_period = bp["lp_period"]
        log.info("Using best params for single-run: κ=%.4f hp=%d lp=%d",
                 args.kappa, args.hp_period, args.lp_period)

    # ── BSI + Roofing Filter + Z-score ────────────────────────────────────
    log.info("Computing BSI (κ=%.3f, hp=%d, lp=%d) …",
             args.kappa, args.hp_period, args.lp_period)
    bars = compute_bsi(bars, kappa=args.kappa,
                       hp_period=args.hp_period, lp_period=args.lp_period)

    # ── TRAdj EMA regime filter (optional) ────────────────────────────────
    if args.regime_filter:
        log.info("Computing TRAdj EMA regime (periods=%d, pds=%d, mltp=%.1f) …",
                 args.regime_periods, args.regime_pds, args.regime_mltp)
        bars = compute_regime(bars,
                              periods    = args.regime_periods,
                              pds        = args.regime_pds,
                              mltp_tradj = args.regime_mltp)
        bull = (bars["regime"] == 1).sum()
        bear = (bars["regime"] == -1).sum()
        log.info("  Bull bars: %d  Bear bars: %d  (%.1f%% bull)",
                 bull, bear, 100 * bull / len(bars))

    if args.save_bars:
        bars.to_parquet(args.save_bars, index=False)
        log.info("Bars saved → %s", args.save_bars)

    # ── Build vectorbt portfolio ───────────────────────────────────────────
    log.info("Building portfolio (threshold=%.1fσ, short=%s, regime=%s) …",
             args.bsi_threshold, not args.no_short, args.regime_filter)
    pf = build_portfolio(
        bars,
        bsi_threshold = args.bsi_threshold,
        allow_short   = not args.no_short,
        use_regime    = args.regime_filter,
        init_cash     = args.init_cash,
        size          = args.size,
        fees          = args.fees,
        freq          = args.bar,
    )

    # ── Stats ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  OHLC-BSI BACKTEST RESULTS  (vectorbt)")
    print("=" * 60)
    print(pf.stats().to_string())
    print("=" * 60)

    # Exit-type breakdown
    trades_df = pf.trades.records_readable
    if not trades_df.empty and "Exit Type" in trades_df.columns:
        print("\nExit type breakdown:")
        print(trades_df["Exit Type"].value_counts().to_string())
        print()

    # ── Save trade log ────────────────────────────────────────────────────
    if args.output:
        trades_df.to_csv(args.output, index=False)
        log.info("Trade log saved → %s", args.output)

    # ── Plot ──────────────────────────────────────────────────────────────
    if args.plot:
        import matplotlib.pyplot as plt

        # 1. BSI Z-score panel (matplotlib)
        regime_tag = f"  regime=TRAdj({args.regime_periods})" if args.regime_filter else ""
        plot_bsi_panel(
            bars, pf,
            bsi_threshold = args.bsi_threshold,
            use_regime    = args.regime_filter,
            title = (f"OHLC-BSI Mean-Reversion  "
                     f"κ={args.kappa}  bar={args.bar}  "
                     f"threshold=±{args.bsi_threshold:.1f}σ{regime_tag}"),
        )
        plt.show()

        # 2. vectorbt interactive portfolio chart
        pf.plot().show()


if __name__ == "__main__":
    main()
