#!/usr/bin/env python3
"""
Backtest: Hawkes-BSI Quantile-Breakout on OHLC bars  (vectorbt engine)

Pipeline:
  ClickHouse table `ohlc_5m` (per symbol) → Hawkes BSI
  → Rolling 5%/95% quantiles → price-direction filter
  → directional signals → vectorbt Portfolio

Hawkes BSI
  BSI[i] = BSI[i-1] * exp(-κ) + (buyvolume[i] - sellvolume[i])

Rolling quantiles  (no lookahead)
  q_lo[i] = 5th-percentile of BSI over last `quantile_lookback` bars
  q_hi[i] = 95th-percentile of BSI over last `quantile_lookback` bars

Signal logic
  ENTRY: BSI crosses above q_hi (first bar >= q_hi after coming from below)
    price_now > price_at_last_below_q_lo → LONG
    price_now < price_at_last_below_q_lo → SHORT
  EXIT: BSI drops back below q_lo → close position at next bar open

Usage
  python backtest_hawkes_quant.py --symbol VN30F1M --plot
  python backtest_hawkes_quant.py --symbol VCB --ch-table ohlc_5m \\
      --kappa 0.2 --quantile-lookback 400 --bar 5m

Connection: set CLICKHOUSE_HOST, CLICKHOUSE_PORT, CLICKHOUSE_USER,
CLICKHOUSE_PASSWORD, CLICKHOUSE_DATABASE or pass --ch-* overrides.
"""

import argparse
import logging
import os
import re

import numpy as np
import pandas as pd

from core.hawkes_indicators import (  # noqa: F401 – re-exported for callers
    compute_hawkes_bsi,
    compute_kama,
    compute_alma,
    generate_signals,
)

try:
    import vectorbt as vbt
except ImportError as e:
    raise ImportError("vectorbt is required: pip install vectorbt") from e

try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
except ImportError:
    optuna = None  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def _validate_sql_identifier(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return name


def load_ohlc_from_clickhouse(
    symbol: str,
    table: str = "ohlc_5m",
    *,
    host: str | None = None,
    port: int | None = None,
    username: str | None = None,
    password: str | None = None,
    database: str | None = None,
) -> pd.DataFrame:
    """Load OHLC rows for one symbol from ClickHouse."""
    try:
        import clickhouse_connect
    except ImportError as e:
        raise ImportError(
            "clickhouse-connect is required: pip install clickhouse-connect"
        ) from e

    h  = host     or os.environ.get("CLICKHOUSE_HOST",     "192.168.1.3")
    p  = int(port or os.environ.get("CLICKHOUSE_PORT",     "8123"))
    u  = username or os.environ.get("CLICKHOUSE_USER",     "kyostyle1")
    pw = password or os.environ.get("CLICKHOUSE_PASSWORD", "kyostyle1")
    db = database or os.environ.get("CLICKHOUSE_DATABASE", "default")

    tbl = _validate_sql_identifier(table)
    _validate_sql_identifier(db)

    client = clickhouse_connect.get_client(
        host=h, port=p, username=u, password=pw, database=db,
    )

    q = f"""
    SELECT
        ts AS stamp,
        open,
        high,
        low,
        close,
        toFloat64(volume)      AS volume,
        toFloat64(buy_volume)  AS buyvolume,
        toFloat64(sell_volume) AS sellvolume
    FROM {db}.{tbl}
    WHERE symbol = {{sym:String}}
    ORDER BY ts ASC
    """
    df = client.query_df(q, parameters={"sym": symbol})
    if df.empty:
        raise ValueError(f"No rows in {db}.{tbl} for symbol={symbol!r}")

    st = pd.to_datetime(df["stamp"])
    if getattr(st.dtype, "tz", None) is not None:
        st = st.dt.tz_convert("Asia/Ho_Chi_Minh").map(
            lambda t: t.replace(tzinfo=None) if t.tzinfo else t
        )
    df["stamp"] = st
    log.info("Loaded %d OHLC rows from %s.%s (%s)", len(df), db, tbl, symbol)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 – vectorbt Portfolio
# ─────────────────────────────────────────────────────────────────────────────

def build_portfolio(
    bars: pd.DataFrame,
    allow_short: bool = True,
    use_kama_gate: bool = True,
    sl_bars: int = 10,
    calm_bars: int = 5,
    calm_threshold: float = 500.0,
    init_cash: float = 1_000_000.0,
    size: float = 1.0,
    fees: float = 0.0,
    freq: str = "5m",
) -> "vbt.Portfolio":
    long_e, short_e, long_x, short_x = generate_signals(
        bars, allow_short=allow_short, use_kama_gate=use_kama_gate, sl_bars=sl_bars,
        calm_bars=calm_bars, calm_threshold=calm_threshold,
    )

    idx = pd.DatetimeIndex(bars["stamp"])

    pf = vbt.Portfolio.from_signals(
        close         = pd.Series(bars["close"].values, index=idx),
        open          = pd.Series(bars["open"].values,  index=idx),
        high          = pd.Series(bars["high"].values,  index=idx),
        low           = pd.Series(bars["low"].values,   index=idx),
        entries       = pd.Series(long_e,  index=idx),
        short_entries = pd.Series(short_e, index=idx),
        exits         = pd.Series(long_x,  index=idx),
        short_exits   = pd.Series(short_x, index=idx),
        upon_opposite_entry = 0,   # state machine prevents re-entry
        init_cash  = init_cash,
        size       = 10,
        size_type  = "percent",
        fees       = fees,
        freq       = freq,
    )
    return pf


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 – Interactive panel plot
# ─────────────────────────────────────────────────────────────────────────────

def _trade_pnl_value(t) -> float | None:
    for col in ("PnL", "P&L", "Return"):
        v = t.get(col)
        if v is not None and not (isinstance(v, float) and np.isnan(v)):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def plot_hawkes_panel(bars: pd.DataFrame, pf: "vbt.Portfolio", title: str = "", ma_label: str = "Gate MA"):
    """
    Three-panel interactive Plotly chart.
    Panel 1: OHLC candles + trade markers/bands
    Panel 2: Hawkes BSI line with q_lo/q_hi quantile band (filled)
    Panel 3: Cumulative return (%)
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        log.warning("plotly not installed – pip install plotly  (panel skipped)")
        return None

    ts    = pd.to_datetime(bars["stamp"], errors="coerce")
    x_bar = np.arange(len(bars), dtype=float)
    n     = len(bars)

    o_arr    = bars["open"].to_numpy(float)
    h_arr    = bars["high"].to_numpy(float)
    l_arr    = bars["low"].to_numpy(float)
    c_arr    = bars["close"].to_numpy(float)
    bsi_arr  = bars["bsi"].to_numpy(float)
    q_lo_arr = bars["q_lo"].to_numpy(float)
    q_hi_arr = bars["q_hi"].to_numpy(float)

    hover_time = ts.dt.strftime("%Y-%m-%d %H:%M").where(ts.notna(), "").tolist()
    candle_hover = [
        f"{hover_time[i] or '—'}<br>"
        f"open={o_arr[i]:.4f} high={h_arr[i]:.4f} "
        f"low={l_arr[i]:.4f} close={c_arr[i]:.4f}"
        for i in range(n)
    ]

    # X ticks: first bar of each calendar day (subsample when history is long)
    day_key = ts.dt.normalize()
    tick_vals: list[float] = []
    tick_text: list[str]   = []
    prev_d = object()
    for i in range(n):
        if pd.isna(day_key.iloc[i]):
            continue
        d = day_key.iloc[i]
        if d != prev_d:
            tick_vals.append(float(i))
            tick_text.append(ts.iloc[i].strftime("%Y-%m-%d"))
            prev_d = d
    max_ticks = 28
    if len(tick_vals) > max_ticks:
        step = int(np.ceil(len(tick_vals) / max_ticks))
        tick_vals = tick_vals[::step]
        tick_text = tick_text[::step]

    price_span = float(np.nanmax(h_arr) - np.nanmin(l_arr))
    if not np.isfinite(price_span) or price_span <= 0:
        price_span = abs(float(np.nanmedian(c_arr))) * 0.01 or 1.0
    marker_y_pad   = max(price_span * 0.004, 1e-12)
    price_axis_pad = price_span * 0.025

    trades_df = pf.trades.records_readable
    if not trades_df.empty:
        trades_df = trades_df.copy()

    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        row_heights=[0.5, 0.25, 0.25],
        subplot_titles=(
            "OHLC & trades",
            "Hawkes BSI  (5%/95% quantile band)",
            "Cumulative return (%)",
        ),
    )

    # ── Panel 1: OHLC ─────────────────────────────────────────────────────
    fig.add_trace(
        go.Candlestick(
            x=x_bar, open=o_arr, high=h_arr, low=l_arr, close=c_arr,
            hovertext=candle_hover, hoverinfo="text",
            name="OHLC",
            increasing_line_color="#26a69a", increasing_fillcolor="#26a69a",
            decreasing_line_color="#ef5350", decreasing_fillcolor="#ef5350",
            line=dict(width=1),
        ),
        row=1, col=1,
    )

    if not trades_df.empty:
        stamp_to_idx = {s: i for i, s in enumerate(bars["stamp"].to_numpy())}
        for _, t in trades_df.iterrows():
            ei = stamp_to_idx.get(t.get("Entry Timestamp"))
            xi = stamp_to_idx.get(t.get("Exit Timestamp"))
            if ei is None or xi is None:
                continue
            is_long    = str(t.get("Direction", "")).lower() == "long"
            band_color = "green" if is_long else "red"
            fig.add_vrect(
                x0=x_bar[ei], x1=x_bar[xi],
                fillcolor=band_color, opacity=0.08, line_width=0, layer="below",
            )
            ep   = float(t.get("Avg Entry Price", c_arr[ei]))
            xp   = float(t.get("Avg Exit Price",  c_arr[xi]))
            ep_y = ep - marker_y_pad if is_long else ep + marker_y_pad
            fig.add_trace(
                go.Scatter(
                    x=[x_bar[ei]], y=[ep_y], mode="markers",
                    marker=dict(
                        symbol="triangle-up" if is_long else "triangle-down",
                        size=10, color=band_color, line=dict(width=0),
                    ),
                    name="Entry", legendgroup="entries", showlegend=False,
                    hovertext=f"entry @ {ep:.6g}", hoverinfo="text",
                ),
                row=1, col=1,
            )
            exit_reason = str(t.get("Exit Type", "")).lower()
            is_tp       = "target" in exit_reason or "tp" in exit_reason
            pnl_val     = _trade_pnl_value(t)
            is_profit   = pnl_val is not None and pnl_val > 0
            exit_symbol = "star" if is_tp else "x"
            exit_color  = "gold" if is_tp else ("mediumseagreen" if is_profit else "crimson")
            xp_y = xp + marker_y_pad if is_long else xp - marker_y_pad
            fig.add_trace(
                go.Scatter(
                    x=[x_bar[xi]], y=[xp_y], mode="markers",
                    marker=dict(symbol=exit_symbol, size=11, color=exit_color, line=dict(width=0)),
                    name="Exit", legendgroup="exits", showlegend=False,
                    hovertext=f"exit @ {xp:.6g}", hoverinfo="text",
                ),
                row=1, col=1,
            )

    if "kama" in bars.columns:
        ma_arr = bars["kama"].to_numpy(float)
        fig.add_trace(
            go.Scatter(
                x=x_bar, y=ma_arr, mode="lines",
                line=dict(color="gold", width=1.5),
                name=ma_label,
                text=hover_time,
                hovertemplate=f"%{{text}}<br>{ma_label}=%{{y:.4f}}<extra></extra>",
            ),
            row=1, col=1,
        )

    p_lo = float(np.nanmin(l_arr))
    p_hi = float(np.nanmax(h_arr))
    fig.update_yaxes(
        title_text="Price",
        range=[p_lo - price_axis_pad, p_hi + price_axis_pad],
        row=1, col=1,
    )

    # ── Panel 2: Hawkes BSI + quantile band ───────────────────────────────
    # Filled region between q_lo and q_hi
    x_fill = np.concatenate([x_bar, x_bar[::-1]])
    y_fill = np.concatenate([q_hi_arr, q_lo_arr[::-1]])
    fig.add_trace(
        go.Scatter(
            x=x_fill, y=y_fill,
            fill="toself", fillcolor="rgba(100,149,237,0.15)",
            line=dict(width=0), name="5%–95% band",
            showlegend=True, hoverinfo="skip",
        ),
        row=2, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x_bar, y=q_hi_arr, mode="lines",
            line=dict(color="salmon", width=1, dash="dash"),
            name="q_hi (95%)",
            text=hover_time,
            hovertemplate="%{text}<br>q_hi=%{y:.3f}<extra></extra>",
        ),
        row=2, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x_bar, y=q_lo_arr, mode="lines",
            line=dict(color="mediumseagreen", width=1, dash="dash"),
            name="q_lo (5%)",
            text=hover_time,
            hovertemplate="%{text}<br>q_lo=%{y:.3f}<extra></extra>",
        ),
        row=2, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x_bar, y=bsi_arr, mode="lines",
            line=dict(color="#10a4f4", width=1),
            name="Hawkes BSI",
            text=hover_time,
            hovertemplate="%{text}<br>BSI=%{y:.3f}<extra></extra>",
        ),
        row=2, col=1,
    )
    fig.add_hline(y=0, line_dash="dash", line_color="gray", line_width=1, row=2, col=1)
    fig.update_yaxes(title_text="Hawkes BSI", row=2, col=1)

    # ── Panel 3: cumulative return ─────────────────────────────────────────
    cum_returns = pf.cumulative_returns()
    cr_pct  = cum_returns.to_numpy(dtype=float).ravel() * 100.0
    cr_x    = x_bar
    cr_hover = hover_time
    if len(cr_pct) != len(cr_x):
        m = min(len(cr_pct), len(cr_x))
        cr_pct = cr_pct[:m]; cr_x = cr_x[:m]; cr_hover = hover_time[:m]
    fig.add_trace(
        go.Scatter(
            x=cr_x, y=cr_pct, mode="lines", name="Cum return %",
            line=dict(color="darkorange", width=1.2),
            fill="tozeroy", fillcolor="rgba(255,140,0,0.14)",
            text=cr_hover,
            hovertemplate="%{text}<br>cum=%{y:.3f}%<extra></extra>",
        ),
        row=3, col=1,
    )
    fig.add_hline(y=0, line_dash="dash", line_color="gray", line_width=1, row=3, col=1)
    fig.update_yaxes(title_text="Cum return (%)", row=3, col=1)

    fig.update_layout(
        title=dict(
            text=title or "Hawkes BSI Quantile-Breakout (vectorbt)",
            x=0.5, xanchor="center",
        ),
        height=950, width=1200,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        dragmode="pan",
    )
    if not tick_vals:
        tick_vals, tick_text = [0.0], [""]
    fig.update_xaxes(
        rangeslider=dict(visible=False),
        tickmode="array", tickvals=tick_vals, ticktext=tick_text,
    )
    fig.update_xaxes(
        title_text="Bar index (contiguous; off-session time removed)",
        row=3, col=1,
    )
    config = {"scrollZoom": True, "displayModeBar": True}
    fig.show(config=config)
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 – Optuna hyper-parameter search
# ─────────────────────────────────────────────────────────────────────────────

def run_optuna(
    base_bars: pd.DataFrame,
    n_trials: int = 200,
    allow_short: bool = True,
    use_kama_gate: bool = True,
    ma_type: str = "kama",
    sl_bars: int = 10,
    calm_bars: int = 5,
    calm_threshold: float = 500.0,
    init_cash: float = 100_000.0,
    size: float = 20_000.0,
    fees: float = 15.0,
    freq: str = "5m",
    min_trades: int = 10,
    n_jobs: int = 1,
    study_name: str = "hawkes_quant",
    storage: str | None = None,
) -> "optuna.Study":
    """
    Search over (kappa, quantile_lookback) to maximise Total Return [%].

    kappa             : [0.05, 0.5]  float step 0.05
    quantile_lookback : [10,   500]  int
    """
    if optuna is None:
        raise ImportError("optuna is required: pip install optuna")

    def objective(trial: "optuna.Trial") -> float:
        kappa = trial.suggest_float("kappa",             0.05, 0.5, step=0.05)
        qlb   = trial.suggest_int(  "quantile_lookback", 10,   500)

        try:
            bars = compute_hawkes_bsi(base_bars, kappa=kappa, quantile_lookback=qlb)
            if use_kama_gate:
                if ma_type == "alma":
                    bars = compute_alma(bars)
                    bars["kama"] = bars["alma"]
                else:
                    bars = compute_kama(bars)
            pf   = build_portfolio(
                bars, allow_short=allow_short, use_kama_gate=use_kama_gate,
                sl_bars=sl_bars, calm_bars=calm_bars, calm_threshold=calm_threshold,
                init_cash=init_cash, size=size, fees=fees, freq=freq,
            )
        except Exception:
            raise optuna.exceptions.TrialPruned()

        stats    = pf.stats()
        n_closed = int(stats.get("Total Closed Trades", 0))
        if n_closed < min_trades:
            raise optuna.exceptions.TrialPruned()

        total_return = float(stats.get("Total Return [%]", float("-inf")))
        if not np.isfinite(total_return):
            raise optuna.exceptions.TrialPruned()
        return total_return

    sampler = optuna.samplers.TPESampler(seed=42)
    study   = optuna.create_study(
        direction      = "maximize",
        sampler        = sampler,
        study_name     = study_name,
        storage        = storage,
        load_if_exists = storage is not None,
    )
    log.info("Starting Optuna: %d trials, n_jobs=%d …", n_trials, n_jobs)
    study.optimize(objective, n_trials=n_trials, n_jobs=n_jobs, show_progress_bar=True)
    return study


def print_optuna_results(study: "optuna.Study") -> None:
    trials_df = study.trials_dataframe(attrs=("number", "value", "params", "state"))
    completed = trials_df[trials_df["state"] == "COMPLETE"].copy()
    completed.sort_values("value", ascending=False, inplace=True)

    print("\n" + "=" * 60)
    print("  OPTUNA RESULTS  –  objective: Total Return [%]")
    print("=" * 60)
    print(f"\n{'Rank':<5} {'Trial':>6}  {'Return%':>9}  {'kappa':>8}  {'lookback':>9}")
    print("-" * 46)
    for rank, (_, row) in enumerate(completed.head(5).iterrows(), 1):
        print(
            f"{rank:<5} {int(row['number']):>6}  {row['value']:>9.4f}  "
            f"{row['params_kappa']:>8.4f}  "
            f"{int(row['params_quantile_lookback']):>9}"
        )

    if completed.empty:
        print("(no completed trials — all pruned or failed)")
        print("=" * 60)
        return

    bp = study.best_params
    bv = study.best_value
    print(f"\nBest Return         : {bv:.4f}%")
    print(f"  kappa             = {bp['kappa']:.4f}")
    print(f"  quantile_lookback = {bp['quantile_lookback']}")
    print("=" * 60)
    print(
        f"\nRun best params:\n"
        f"  python backtest_hawkes_quant.py --symbol SYMBOL "
        f"--kappa {bp['kappa']:.4f} "
        f"--quantile-lookback {bp['quantile_lookback']} --plot\n"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Hawkes-BSI Quantile-Breakout Backtest  (vectorbt)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples
  python backtest_hawkes_quant.py --symbol VN30F1M --plot
  python backtest_hawkes_quant.py --symbol VCB --ch-table ohlc_5m \\
      --kappa 0.2 --quantile-lookback 400 --bar 5m
  python backtest_hawkes_quant.py --symbol VN30F1M --optimize --n-trials 300 --plot
        """,
    )
    # ── Symbol / ClickHouse ────────────────────────────────────────────────
    parser.add_argument("--symbol",       required=True,
                        help="Symbol (e.g. VN30F1M, VCB)")
    parser.add_argument("--ch-table",     default="ohlc_5m",
                        help="ClickHouse OHLC table (default: ohlc_5m)")
    parser.add_argument("--ch-host",      default=None)
    parser.add_argument("--ch-port",      type=int, default=None)
    parser.add_argument("--ch-user",      default=None)
    parser.add_argument("--ch-password",  default=None)
    parser.add_argument("--ch-database",  default=None)
    parser.add_argument("--bar",          default="5m",
                        help="vectorbt bar frequency label (default: 5m)")
    # ── Strategy params ────────────────────────────────────────────────────
    parser.add_argument("--kappa",             type=float, default=0.1,
                        help="Hawkes decay κ  (default 0.1)")
    parser.add_argument("--quantile-lookback", type=int,   default=20,
                        help="Rolling window in bars for quantile bands "
                             "(default 20; ~1000 bars ≈ 20 trading days at 5m)")
    parser.add_argument("--q-lo",              type=float, default=5.0,
                        help="Lower quantile pct  (default 5.0)")
    parser.add_argument("--q-hi",              type=float, default=95.0,
                        help="Upper quantile pct  (default 95.0)")
    parser.add_argument("--no-short",          action="store_true",
                        help="Disable short trades")
    # ── Trend MA gate ──────────────────────────────────────────────────────
    parser.add_argument("--ma-type",      default="kama", choices=["kama", "alma", "none"],
                        help="Trend MA for gate filter: kama | alma | none (default kama)")
    parser.add_argument("--kama-period",  type=int,   default=10,
                        help="KAMA efficiency-ratio lookback (default 10)")
    parser.add_argument("--kama-fast",    type=int,   default=2,
                        help="KAMA fast EMA period (default 2)")
    parser.add_argument("--kama-slow",    type=int,   default=30,
                        help="KAMA slow EMA period (default 30)")
    parser.add_argument("--alma-window",  type=int,   default=9,
                        help="ALMA window size (default 9)")
    parser.add_argument("--alma-offset",  type=float, default=0.85,
                        help="ALMA offset 0–1, controls peak weight position (default 0.85)")
    parser.add_argument("--alma-sigma",   type=float, default=6.0,
                        help="ALMA sigma, controls Gaussian width (default 6)")
    parser.add_argument("--no-kama-gate", action="store_true",
                        help="Disable the trend MA gate entirely (equivalent to --ma-type none)")
    parser.add_argument("--sl-bars",         type=int,   default=10,
                        help="SL window: bars after entry while SL is active (default 10, 0=disabled)")
    parser.add_argument("--calm-bars",       type=int,   default=5,
                        help="Calm gate lookback: bars to check for recent BSI calm (default 5)")
    parser.add_argument("--calm-threshold",  type=float, default=500.0,
                        help="Calm gate: abs(BSI) must be below this in at least 1 of last calm-bars bars (default 500)")
    # ── Portfolio params ───────────────────────────────────────────────────
    parser.add_argument("--init-cash", type=float, default=10_000_000,
                        help="Starting capital (default 10 000 000)")
    parser.add_argument("--size",      type=float, default=1,
                        help="Position size in units (default 1)")
    parser.add_argument("--fees",      type=float, default=0,
                        help="Fee per trade as fraction (default 0)")
    # ── Output / plot ──────────────────────────────────────────────────────
    parser.add_argument("--save-bars", default=None, help="Save OHLC+BSI bars to parquet")
    parser.add_argument("--output",    default=None, help="Save trade log to CSV")
    parser.add_argument("--plot",      action="store_true",
                        help="Show interactive panel (Plotly) + portfolio chart")
    # ── Optuna ────────────────────────────────────────────────────────────
    parser.add_argument("--optimize",      action="store_true",
                        help="Run Optuna search over kappa and quantile-lookback")
    parser.add_argument("--n-trials",      type=int, default=200,
                        help="Optuna trial count (default 200)")
    parser.add_argument("--n-jobs",        type=int, default=1,
                        help="Parallel Optuna workers (default 1)")
    parser.add_argument("--min-trades",    type=int, default=10,
                        help="Min closed trades to accept a trial (default 10)")
    parser.add_argument("--study-storage", default=None,
                        help="Optuna storage URL, e.g. sqlite:///optuna.db")
    args = parser.parse_args()

    # ── Load data ──────────────────────────────────────────────────────────
    bars = load_ohlc_from_clickhouse(
        args.symbol,
        table    = args.ch_table,
        host     = args.ch_host,
        port     = args.ch_port,
        username = args.ch_user,
        password = args.ch_password,
        database = args.ch_database,
    )

    use_gate = not args.no_kama_gate and args.ma_type != "none"

    # ── Optuna (optional) ──────────────────────────────────────────────────
    if args.optimize:
        study = run_optuna(
            base_bars      = bars,
            n_trials       = args.n_trials,
            allow_short    = not args.no_short,
            use_kama_gate  = use_gate,
            ma_type        = args.ma_type,
            sl_bars        = args.sl_bars,
            calm_bars      = args.calm_bars,
            calm_threshold = args.calm_threshold,
            init_cash      = args.init_cash,
            size           = args.size,
            fees           = args.fees,
            freq           = args.bar,
            min_trades     = args.min_trades,
            n_jobs         = args.n_jobs,
            storage        = args.study_storage,
        )
        print_optuna_results(study)
        try:
            bp = study.best_params
        except ValueError:
            log.warning("No completed Optuna trials — keeping CLI params.")
        else:
            args.kappa             = bp["kappa"]
            args.quantile_lookback = bp["quantile_lookback"]
            log.info(
                "Using best params: κ=%.4f lookback=%d",
                args.kappa, args.quantile_lookback,
            )

    # ── Compute BSI + quantile bands ───────────────────────────────────────
    log.info(
        "Computing Hawkes BSI (κ=%.3f, lookback=%d, q=%.0f%%/%.0f%%) …",
        args.kappa, args.quantile_lookback, args.q_lo, args.q_hi,
    )
    bars = compute_hawkes_bsi(
        bars,
        kappa             = args.kappa,
        quantile_lookback = args.quantile_lookback,
        q_lo_pct          = args.q_lo,
        q_hi_pct          = args.q_hi,
    )

    if use_gate:
        if args.ma_type == "alma":
            log.info("Computing ALMA (window=%d, offset=%.2f, sigma=%.1f) …",
                     args.alma_window, args.alma_offset, args.alma_sigma)
            bars = compute_alma(bars, window=args.alma_window,
                                offset=args.alma_offset, sigma=args.alma_sigma)
            bars["kama"] = bars["alma"]
            ma_label = f"ALMA({args.alma_window},{args.alma_offset},{args.alma_sigma})"
        else:
            log.info("Computing KAMA (period=%d, fast=%d, slow=%d) …",
                     args.kama_period, args.kama_fast, args.kama_slow)
            bars = compute_kama(bars, period=args.kama_period,
                                fast=args.kama_fast, slow=args.kama_slow)
            ma_label = f"KAMA({args.kama_period},{args.kama_fast},{args.kama_slow})"
    else:
        ma_label = "none"

    if args.save_bars:
        bars.to_parquet(args.save_bars, index=False)
        log.info("Bars saved → %s", args.save_bars)

    # ── Build portfolio ────────────────────────────────────────────────────
    log.info(
        "Building portfolio (short=%s, kama_gate=%s, sl_bars=%d, calm=%d/<%.0f) …",
        not args.no_short, not args.no_kama_gate, args.sl_bars,
        args.calm_bars, args.calm_threshold,
    )
    pf = build_portfolio(
        bars,
        allow_short    = not args.no_short,
        use_kama_gate  = use_gate,
        sl_bars        = args.sl_bars,
        calm_bars      = args.calm_bars,
        calm_threshold = args.calm_threshold,
        init_cash      = args.init_cash,
        size           = args.size,
        fees           = args.fees,
        freq           = args.bar,
    )

    # ── Stats ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  HAWKES QUANTILE-BREAKOUT BACKTEST RESULTS  (vectorbt)")
    print("=" * 60)
    print(pf.stats().to_string())
    print("=" * 60)

    trades_df = pf.trades.records_readable
    if not trades_df.empty and "Exit Type" in trades_df.columns:
        print("\nExit type breakdown:")
        print(trades_df["Exit Type"].value_counts().to_string())
        print()

    if args.output:
        trades_df.to_csv(args.output, index=False)
        log.info("Trade log saved → %s", args.output)

    # ── Plot ───────────────────────────────────────────────────────────────
    if args.plot:
        plot_hawkes_panel(
            bars, pf,
            ma_label=ma_label,
            title=(
                f"Hawkes BSI Quantile-Breakout  "
                f"κ={args.kappa}  bar={args.bar}  "
                f"lookback={args.quantile_lookback}  "
                f"q={args.q_lo:.0f}%/{args.q_hi:.0f}%  "
                f"{ma_label}"
            ),
        )
        pf.plot().show()


if __name__ == "__main__":
    main()
