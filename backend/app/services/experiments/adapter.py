"""The only vectorbt-aware module in the experiment store.

vectorbt renames record columns between releases. Every column this module
depends on is listed in REQUIRED_RECORD_COLUMNS and checked up front, so an
incompatible version fails loudly at log time instead of writing NULLs.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.experiments.schema import CORE_TRADE_COLUMNS

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
