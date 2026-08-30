"""Column contracts and value cleaning for the experiment store.

Pure helpers: no I/O, no vectorbt. Everything here is safe to import from
tests and from notebooks.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime
from typing import Any, Mapping

import numpy as np
import pandas as pd

SCHEMA_VERSION = 1
FEATURE_PREFIX = "feat_"

CORE_TRADE_COLUMNS = [
    "run_id", "trade_id", "symbol",
    "entry_dt", "entry_price", "exit_dt", "exit_price",
    "size", "pnl", "ret", "net_return",
    "bars_held", "direction", "status", "exit_reason",
]

SYMBOL_STATS_COLUMNS = [
    "run_id", "symbol", "n_trades", "total_return", "sharpe", "sortino",
    "max_drawdown", "win_rate", "avg_win", "avg_loss", "profit_factor",
    "expectancy", "exposure",
]

EQUITY_COLUMNS = ["run_id", "dt", "value", "returns", "drawdown", "benchmark_value"]

OUTCOME_LABELS = [
    "1_catastrophic_loss", "2_medium_loss", "3_marginal",
    "4_medium_win", "5_big_win",
]
DEFAULT_QUANTILES = [0.10, 0.30, 0.70, 0.90]

_UNSAFE_NAME = re.compile(r"[^0-9A-Za-z._-]+")


def clean_float(value: Any) -> float | None:
    """Coerce to a JSON/Parquet-safe float, mapping NaN and +/-inf to None.

    vectorbt returns inf for zero-downside symbols (sortino) and zero-loss
    symbols (profit factor); those must not reach Parquet as inf, or SQL
    aggregates over them become inf too.
    """
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(out) else out


def json_safe(obj: Any) -> Any:
    """Recursively convert numpy/pandas values into JSON-serialisable ones."""
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return clean_float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp, datetime, date)):
        return pd.Timestamp(obj).isoformat()
    if isinstance(obj, np.ndarray):
        return [json_safe(v) for v in obj.tolist()]
    if isinstance(obj, (str, int, bool)):
        return obj
    return str(obj)


def params_hash(params: Mapping[str, Any] | None) -> str:
    """Six hex chars, stable across key ordering and numpy scalar types."""
    payload = json.dumps(json_safe(dict(params or {})), sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:6]


def make_run_id(name: str, params: Mapping[str, Any] | None, created_at: datetime) -> str:
    safe_name = _UNSAFE_NAME.sub("-", name).strip("-") or "run"
    stamp = created_at.strftime("%Y%m%dT%H%M%S")
    return f"{safe_name}__{stamp}__{params_hash(params)}"
