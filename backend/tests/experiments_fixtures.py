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


def make_multiindex_portfolio():
    """A portfolio whose columns carry parameter levels ahead of the symbol.

    Any parameterised indicator produces this: vectorbt puts the named param
    levels first and leaves the original symbol level last, unnamed. This is
    the shape real notebooks produce, and the reason symbols must be read from
    a level rather than stringified off the tuple.

    Signals are set explicitly rather than derived from a crossover, so the
    portfolio actually trades — a fixture with zero trades makes assertions
    about symbol labels pass vacuously.
    """
    idx = pd.date_range("2024-01-01", periods=40, freq="D")
    close = pd.DataFrame(
        {"AAA": np.linspace(10, 15, 40), "BBB": np.linspace(20, 18, 40)}, index=idx
    )
    # Borrow the MultiIndex column shape a parameterised indicator produces.
    wide = vbt.MA.run(close, window=[5], short_name="fast").ma
    wide_close = pd.DataFrame(
        np.column_stack([close["AAA"], close["BBB"]]), index=idx, columns=wide.columns,
    )
    entries = pd.DataFrame(False, index=idx, columns=wide.columns)
    exits = pd.DataFrame(False, index=idx, columns=wide.columns)
    entries.iloc[[3, 20], :] = True
    exits.iloc[[10, 28], :] = True
    return vbt.Portfolio.from_signals(
        close=wide_close, entries=entries, exits=exits,
        freq="1d", cash_sharing=False, init_cash=100,
    )


def make_param_sweep_portfolio():
    """Two parameter combinations over the same symbols.

    The store records one `params` dict per run, so a sweep cannot be logged
    coherently — the adapter must reject it rather than emit duplicate rows.
    """
    idx = pd.date_range("2024-01-01", periods=40, freq="D")
    close = pd.DataFrame(
        {"AAA": np.linspace(10, 15, 40), "BBB": np.linspace(20, 18, 40)}, index=idx
    )
    wide = vbt.MA.run(close, window=[5, 10], short_name="fast").ma
    wide_close = pd.DataFrame(
        np.column_stack([close["AAA"], close["BBB"], close["AAA"], close["BBB"]]),
        index=idx, columns=wide.columns,
    )
    entries = pd.DataFrame(False, index=idx, columns=wide.columns)
    exits = pd.DataFrame(False, index=idx, columns=wide.columns)
    entries.iloc[[3, 20], :] = True
    exits.iloc[[10, 28], :] = True
    return vbt.Portfolio.from_signals(
        close=wide_close, entries=entries, exits=exits,
        freq="1d", cash_sharing=False, init_cash=100,
    )
