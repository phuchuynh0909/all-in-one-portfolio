"""The only vectorbt-aware module in the experiment store.

vectorbt renames record columns between releases. Every column this module
depends on is listed in REQUIRED_RECORD_COLUMNS and checked up front, so an
incompatible version fails loudly at log time instead of writing NULLs.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.experiments.schema import (
    CORE_TRADE_COLUMNS,
    EQUITY_COLUMNS,
    SYMBOL_STATS_COLUMNS,
    clean_float,
)

REQUIRED_RECORD_COLUMNS = [
    "id", "col", "size", "entry_idx", "entry_price", "entry_fees",
    "exit_idx", "exit_price", "exit_fees", "pnl", "return", "direction", "status",
]

# vectorbt.portfolio.enums.TradeDirection / TradeStatus, inlined so the module
# does not depend on the enum import path surviving upgrades.
_DIRECTION = {0: "long", 1: "short"}
_STATUS = {0: "open", 1: "closed"}
_STATUS_CLOSED = 1


class UnmappedVectorbtColumns(RuntimeError):
    """Raised when the installed vectorbt exposes an unexpected record schema."""


def _empty_trades() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="object") for c in CORE_TRADE_COLUMNS})


def build_trades(pf, run_id: str) -> pd.DataFrame:
    """Extract one row per exit trade from a vectorbt Portfolio."""
    rec = pf.trades.records
    missing = [c for c in REQUIRED_RECORD_COLUMNS if c not in rec.columns]
    if missing:
        raise UnmappedVectorbtColumns(
            f"vectorbt trade records are missing {missing}; "
            f"got {list(rec.columns)}. Update REQUIRED_RECORD_COLUMNS and the "
            f"extraction in adapter.py for this vectorbt version."
        )
    if len(rec) == 0:
        return _empty_trades()

    columns = pd.Index(pf.wrapper.columns)
    index = pd.DatetimeIndex(pf.wrapper.index)

    entry_idx = rec["entry_idx"].to_numpy(dtype="int64")
    exit_idx = rec["exit_idx"].to_numpy(dtype="int64")
    is_closed = rec["status"].to_numpy() == _STATUS_CLOSED

    exit_dt = pd.Series(index[exit_idx], dtype="datetime64[ns]")
    exit_dt[~is_closed] = pd.NaT
    exit_price = rec["exit_price"].astype(float).to_numpy()
    exit_price = np.where(is_closed, exit_price, np.nan)
    bars_held = np.where(is_closed, (exit_idx - entry_idx).astype(float), np.nan)

    size = rec["size"].astype(float).to_numpy()
    entry_price = rec["entry_price"].astype(float).to_numpy()
    fees = rec["entry_fees"].astype(float).to_numpy() + rec["exit_fees"].astype(float).to_numpy()
    cost = entry_price * size
    with np.errstate(divide="ignore", invalid="ignore"):
        gross = np.where(cost != 0, (rec["pnl"].astype(float).to_numpy() + fees) / cost, np.nan)

    out = pd.DataFrame({
        "run_id": run_id,
        "trade_id": rec["id"].astype("int64").to_numpy(),
        "symbol": columns[rec["col"].to_numpy(dtype="int64")].astype(str),
        "entry_dt": index[entry_idx],
        "entry_price": entry_price,
        "exit_dt": exit_dt.to_numpy(),
        "exit_price": exit_price,
        "size": size,
        "pnl": rec["pnl"].astype(float).to_numpy(),
        "ret": gross,
        "net_return": rec["return"].astype(float).to_numpy(),
        "bars_held": bars_held,
        "direction": [_DIRECTION.get(int(d), "unknown") for d in rec["direction"]],
        "status": [_STATUS.get(int(s), "unknown") for s in rec["status"]],
        # vectorbt does not record why a position closed; callers supply it.
        "exit_reason": pd.Series([None] * len(rec), dtype="object"),
    })
    return out[CORE_TRADE_COLUMNS].reset_index(drop=True)


def _as_symbol_series(value, columns: pd.Index) -> pd.Series:
    """Normalise a vectorbt metric into a Series indexed by symbol."""
    if isinstance(value, pd.Series):
        return value.reindex(columns)
    return pd.Series(np.repeat(np.asarray(value), len(columns))[: len(columns)], index=columns)


def build_symbol_stats(pf, run_id: str, trades: pd.DataFrame) -> pd.DataFrame:
    """One row per symbol. Non-finite metrics are cleaned to NULL."""
    columns = pd.Index(pf.wrapper.columns).astype(str)
    n_bars = len(pf.wrapper.index)

    metrics = {
        "total_return": pf.total_return(),
        "sharpe": pf.sharpe_ratio(),
        "sortino": pf.sortino_ratio(),
        "max_drawdown": pf.max_drawdown(),
        "win_rate": pf.trades.win_rate(),
        "profit_factor": pf.trades.profit_factor(),
        "expectancy": pf.trades.expectancy(),
    }
    frame = pd.DataFrame(
        {name: _as_symbol_series(value, columns).to_numpy() for name, value in metrics.items()},
        index=columns,
    )

    # Derived from the trade frame rather than more vectorbt API surface.
    by_symbol = trades.groupby("symbol", dropna=False)
    n_trades = by_symbol.size().reindex(columns).fillna(0).astype("int64")
    wins = trades[trades["net_return"] > 0].groupby("symbol")["net_return"].mean()
    losses = trades[trades["net_return"] <= 0].groupby("symbol")["net_return"].mean()
    frame["avg_win"] = wins.reindex(columns).to_numpy()
    frame["avg_loss"] = losses.reindex(columns).to_numpy()
    held = by_symbol["bars_held"].sum().reindex(columns).fillna(0)
    frame["exposure"] = (held / n_bars).to_numpy() if n_bars else np.nan

    frame = frame.map(clean_float)
    frame["n_trades"] = n_trades
    frame.insert(0, "symbol", columns)
    frame.insert(0, "run_id", run_id)
    return frame.reset_index(drop=True)[SYMBOL_STATS_COLUMNS]


def build_equity(pf, run_id: str, benchmark: pd.Series | None = None) -> tuple[pd.DataFrame, str]:
    """Portfolio equity curve.

    With cash_sharing=False every symbol is an independent book, so there is
    no single traded curve; the equal-weight mean across symbols is stored and
    labelled agg="mean" so the UI never implies a real portfolio.
    """
    value = pf.value()
    if isinstance(value, pd.DataFrame):
        series, agg = value.mean(axis=1), "mean"
    else:
        series, agg = pd.Series(np.asarray(value), index=pf.wrapper.index), "portfolio"

    running_max = series.cummax()
    drawdown = (series / running_max - 1.0).where(running_max != 0, 0.0)

    bench = (
        pd.Series(np.asarray(benchmark), index=pd.DatetimeIndex(benchmark.index)).reindex(series.index)
        if benchmark is not None
        else pd.Series(np.nan, index=series.index)
    )

    frame = pd.DataFrame({
        "run_id": run_id,
        "dt": pd.DatetimeIndex(series.index),
        "value": series.to_numpy(dtype="float64"),
        "returns": series.pct_change().to_numpy(dtype="float64"),
        "drawdown": drawdown.to_numpy(dtype="float64"),
        "benchmark_value": bench.to_numpy(dtype="float64"),
    })
    frame = frame.replace([np.inf, -np.inf], np.nan)
    return frame[EQUITY_COLUMNS].reset_index(drop=True), agg
