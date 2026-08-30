"""Deterministic synthetic vectorbt portfolios. Offline: no data loader, no network."""
from __future__ import annotations

import numpy as np
import pandas as pd
import vectorbt as vbt


def make_portfolio(*, n_bars: int = 30, grouped: bool = False, no_trades: bool = False):
    """Two symbols, one rising and one falling, with two round trips each."""
    idx = pd.date_range("2024-01-01", periods=n_bars, freq="D")
    close = pd.DataFrame(
        {"AAA": np.linspace(10, 15, n_bars), "BBB": np.linspace(20, 18, n_bars)},
        index=idx,
    )
    entries = pd.DataFrame(False, index=idx, columns=close.columns)
    exits = pd.DataFrame(False, index=idx, columns=close.columns)
    if not no_trades:
        entries.iloc[[2, 15], :] = True
        exits.iloc[[8, 22], :] = True

    kwargs = dict(close=close, entries=entries, exits=exits, freq="1d", init_cash=100)
    if grouped:
        return vbt.Portfolio.from_signals(**kwargs, group_by=True, cash_sharing=True)
    return vbt.Portfolio.from_signals(**kwargs, cash_sharing=False)


def make_open_trade_portfolio():
    """One entry with no exit, so the final trade is still open."""
    idx = pd.date_range("2024-01-01", periods=10, freq="D")
    close = pd.DataFrame({"AAA": np.linspace(10, 15, 10)}, index=idx)
    entries = pd.DataFrame(False, index=idx, columns=close.columns)
    entries.iloc[1, 0] = True
    exits = pd.DataFrame(False, index=idx, columns=close.columns)
    return vbt.Portfolio.from_signals(
        close=close, entries=entries, exits=exits, freq="1d",
        cash_sharing=False, init_cash=100,
    )
