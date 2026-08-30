"""The only vectorbt-aware module in the experiment store.

vectorbt renames record columns between releases. Every column this module
depends on is listed in REQUIRED_RECORD_COLUMNS and checked up front, so an
incompatible version fails loudly at log time instead of writing NULLs.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from typing import Mapping, Sequence

import numpy as np
import pandas as pd
from loguru import logger

from app.services.experiments.schema import (
    CORE_TRADE_COLUMNS,
    EQUITY_COLUMNS,
    FEATURE_PREFIX,
    SYMBOL_STATS_COLUMNS,
    clean_float,
    json_safe,
    make_run_id,
)
from app.services.experiments.store import ExperimentStore, RunHandle

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


class AmbiguousSymbolColumns(ValueError):
    """Raised when several portfolio columns resolve to the same symbol."""


def symbol_labels(columns) -> pd.Index:
    """One symbol label per portfolio column.

    A parameterised indicator makes `pf.wrapper.columns` a MultiIndex: vectorbt
    puts the named parameter levels first and leaves the original symbol level
    last, unnamed. Stringifying the whole index yields tuple reprs like
    "(5, 'AAA')", which is what used to reach the store.
    """
    if isinstance(columns, pd.MultiIndex):
        names = list(columns.names)
        level = names.index("symbol") if "symbol" in names else columns.nlevels - 1
        return pd.Index(columns.get_level_values(level)).astype(str)
    return pd.Index(columns).astype(str)


def _checked_symbol_labels(columns) -> pd.Index:
    labels = symbol_labels(columns)
    if labels.has_duplicates:
        dupes = labels[labels.duplicated()].unique()[:3].tolist()
        raise AmbiguousSymbolColumns(
            f"several portfolio columns map to the same symbol ({dupes}), which "
            "means this Portfolio holds more than one parameter combination. A "
            "run records a single `params` dict, so log one combination per "
            "run — select the parameter set first, then call log_experiment."
        )
    return labels


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

    columns = _checked_symbol_labels(pf.wrapper.columns)
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
        "symbol": columns[rec["col"].to_numpy(dtype="int64")],
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


class MisalignedMetric(RuntimeError):
    """A vectorbt metric did not yield one value per portfolio column."""


def _metric_values(name: str, value, n_columns: int) -> np.ndarray:
    """One float per portfolio column, aligned positionally.

    Deliberately NOT aligned by label. vectorbt returns metric Series indexed
    by the portfolio columns, but that index does not always match
    `wrapper.columns` exactly — extra broadcast levels (e.g. from passing
    `sl_stop` as a DataFrame) or stringified MultiIndex labels make every
    lookup miss, and `reindex` reports that as NaN rather than as an error.
    That silently wrote NULL for every metric of a real 200-symbol run.

    Column order is what vectorbt guarantees, so position is the reliable key,
    and a length mismatch raises instead of degrading to NaN.
    """
    array = np.asarray(value, dtype="float64").ravel()
    if array.size == 1 and n_columns != 1:
        array = np.repeat(array, n_columns)
    if array.size != n_columns:
        raise MisalignedMetric(
            f"vectorbt metric {name!r} returned {array.size} values for "
            f"{n_columns} portfolio columns; cannot align them."
        )
    return array


def build_symbol_stats(pf, run_id: str, trades: pd.DataFrame) -> pd.DataFrame:
    """One row per symbol. Non-finite metrics are cleaned to NULL."""
    columns = _checked_symbol_labels(pf.wrapper.columns)
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
        {name: _metric_values(name, value, len(columns)) for name, value in metrics.items()},
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


class FeatureCollisionError(ValueError):
    """A supplied feature column would overwrite an existing trade column."""


_JOIN_KEYS = ["symbol", "entry_dt"]


def attach_features(trades: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    """Left-join per-trade features, prefixing every value column with feat_."""
    missing = [k for k in _JOIN_KEYS if k not in features.columns]
    if missing:
        raise ValueError(f"features must contain join keys {_JOIN_KEYS}; missing {missing}")

    value_cols = [c for c in features.columns if c not in _JOIN_KEYS]
    renamed = features.rename(columns={c: f"{FEATURE_PREFIX}{c}" for c in value_cols})
    clashing = [c for c in renamed.columns if c not in _JOIN_KEYS and c in trades.columns]
    if clashing:
        raise FeatureCollisionError(
            f"feature columns {clashing} already exist on the trade frame; "
            "rename them before logging"
        )

    out = trades.merge(renamed, on=_JOIN_KEYS, how="left", validate="many_to_one")
    if len(out) != len(trades):
        raise ValueError("feature join changed the trade row count; keys are not unique")
    return out


def _apply_exit_reasons(trades: pd.DataFrame, exit_reasons: pd.DataFrame) -> pd.DataFrame:
    required = _JOIN_KEYS + ["exit_reason"]
    missing = [k for k in required if k not in exit_reasons.columns]
    if missing:
        raise ValueError(f"exit_reasons must contain {required}; missing {missing}")
    return trades.drop(columns=["exit_reason"]).merge(
        exit_reasons[required], on=_JOIN_KEYS, how="left", validate="many_to_one"
    )


def _git_source(notebook: str | None) -> dict:
    def _git(*args: str) -> str | None:
        try:
            return subprocess.run(
                ["git", *args], capture_output=True, text=True, timeout=5, check=True
            ).stdout.strip()
        except Exception:  # git absent, not a repo, or timed out — never fatal
            return None

    return {
        "notebook": notebook,
        "git_sha": _git("rev-parse", "--short", "HEAD"),
        "dirty": bool(_git("status", "--porcelain")) or None,
    }


def log_experiment(
    pf,
    name: str,
    params: Mapping[str, object] | None = None,
    tags: Sequence[str] | None = None,
    features: pd.DataFrame | None = None,
    benchmark: pd.Series | None = None,
    exit_reasons: pd.DataFrame | None = None,
    notes: str | None = None,
    notebook: str | None = None,
    store: ExperimentStore | None = None,
) -> RunHandle:
    """Persist a vectorbt Portfolio as an experiment run."""
    store = store or ExperimentStore.from_env()
    created_at = datetime.now(timezone.utc)
    run_id = make_run_id(name, params, created_at)

    trades = build_trades(pf, run_id=run_id)
    if exit_reasons is not None and len(trades):
        trades = _apply_exit_reasons(trades, exit_reasons)
    if features is not None and len(trades):
        trades = attach_features(trades, features)

    symbol_stats = build_symbol_stats(pf, run_id=run_id, trades=trades)
    equity, equity_agg = build_equity(pf, run_id=run_id, benchmark=benchmark)

    total_return = symbol_stats["total_return"].dropna()
    meta = {
        "run_id": run_id,
        "name": name,
        "created_at": created_at.isoformat(),
        "tags": list(tags or []),
        "params": json_safe(dict(params or {})),
        "notes": notes,
        "data_start": str(pd.Timestamp(pf.wrapper.index[0]).date()),
        "data_end": str(pd.Timestamp(pf.wrapper.index[-1]).date()),
        "n_symbols": int(len(pd.Index(pf.wrapper.columns))),
        "n_trades": int(len(trades)),
        "equity_agg": equity_agg,
        "metrics": {
            "mean_total_return": clean_float(total_return.mean()),
            "mean_sharpe": clean_float(symbol_stats["sharpe"].dropna().mean()),
            "pct_symbols_positive": clean_float((total_return > 0).mean()),
        },
        "source": _git_source(notebook),
        "feature_columns": [c for c in trades.columns if c.startswith(FEATURE_PREFIX)],
    }

    logger.info("logging experiment name={} run_id={} trades={}", name, run_id, len(trades))
    return store.write_run(run_id=run_id, meta=meta, trades=trades,
                           symbol_stats=symbol_stats, equity=equity)
