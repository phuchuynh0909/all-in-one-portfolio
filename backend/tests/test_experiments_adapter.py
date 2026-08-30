"""Adapter tests: vectorbt Portfolio -> store frames. Offline."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services.experiments.adapter import UnmappedVectorbtColumns, build_trades
from app.services.experiments.schema import CORE_TRADE_COLUMNS
from tests.experiments_fixtures import make_open_trade_portfolio, make_portfolio


def test_build_trades_returns_core_columns_in_order():
    df = build_trades(make_portfolio(), run_id="r1")
    assert list(df.columns) == CORE_TRADE_COLUMNS


def test_build_trades_maps_symbol_and_timestamps():
    pf = make_portfolio()
    df = build_trades(pf, run_id="r1")

    assert set(df["symbol"]) == {"AAA", "BBB"}
    assert df["run_id"].unique().tolist() == ["r1"]
    # Entry index 2 on a 2024-01-01 daily index is 2024-01-03.
    first = df[df["symbol"] == "AAA"].iloc[0]
    assert first["entry_dt"] == pd.Timestamp("2024-01-03")
    assert first["exit_dt"] == pd.Timestamp("2024-01-09")
    assert first["bars_held"] == 6


def test_build_trades_net_return_matches_vectorbt_readable_records():
    """The binding assertion: if extraction drifts, this fails.

    Cross-checked against records_readable, an independent vectorbt API from
    the records DataFrame the adapter actually reads.
    """
    pf = make_portfolio()
    df = build_trades(pf, run_id="r1").sort_values("trade_id").reset_index(drop=True)
    readable = pf.trades.records_readable.sort_values("Exit Trade Id").reset_index(drop=True)

    assert len(df) == len(readable) > 0
    np.testing.assert_allclose(df["net_return"].to_numpy(),
                               readable["Return"].to_numpy(), rtol=1e-12)
    np.testing.assert_allclose(df["pnl"].to_numpy(),
                               readable["PnL"].to_numpy(), rtol=1e-12)
    assert df["symbol"].tolist() == readable["Column"].tolist()


def test_build_trades_decodes_direction_and_status():
    df = build_trades(make_portfolio(), run_id="r1")
    assert set(df["direction"]) == {"long"}
    assert set(df["status"]) == {"closed"}


def test_build_trades_nulls_exit_fields_for_open_trades():
    df = build_trades(make_open_trade_portfolio(), run_id="r1")
    open_rows = df[df["status"] == "open"]
    assert len(open_rows) == 1
    assert pd.isna(open_rows.iloc[0]["exit_dt"])
    assert pd.isna(open_rows.iloc[0]["exit_price"])
    assert pd.isna(open_rows.iloc[0]["bars_held"])


def test_build_trades_exit_reason_defaults_to_null():
    df = build_trades(make_portfolio(), run_id="r1")
    assert df["exit_reason"].isna().all()


def test_build_trades_gross_ret_is_at_least_net_return_without_fees():
    df = build_trades(make_portfolio(), run_id="r1")
    # Fixture has zero fees, so gross and net coincide.
    np.testing.assert_allclose(df["ret"].to_numpy(), df["net_return"].to_numpy(), rtol=1e-12)


def test_build_trades_on_empty_portfolio_returns_empty_typed_frame():
    df = build_trades(make_portfolio(no_trades=True), run_id="r1")
    assert len(df) == 0
    assert list(df.columns) == CORE_TRADE_COLUMNS


def test_build_trades_raises_on_missing_vectorbt_column():
    class FakeTrades:
        records = pd.DataFrame({"id": [0], "col": [0]})  # missing everything else

    class FakePf:
        trades = FakeTrades()

    with pytest.raises(UnmappedVectorbtColumns, match="entry_idx"):
        build_trades(FakePf(), run_id="r1")


from app.services.experiments.adapter import build_equity, build_symbol_stats
from app.services.experiments.schema import EQUITY_COLUMNS, SYMBOL_STATS_COLUMNS


def test_build_symbol_stats_has_one_row_per_symbol():
    pf = make_portfolio()
    stats = build_symbol_stats(pf, run_id="r1", trades=build_trades(pf, run_id="r1"))
    assert list(stats.columns) == SYMBOL_STATS_COLUMNS
    assert sorted(stats["symbol"]) == ["AAA", "BBB"]
    assert stats["run_id"].unique().tolist() == ["r1"]


def test_build_symbol_stats_matches_vectorbt_total_return():
    pf = make_portfolio()
    stats = build_symbol_stats(pf, run_id="r1", trades=build_trades(pf, run_id="r1"))
    expected = pf.total_return()
    got = stats.set_index("symbol")["total_return"]
    for symbol, value in expected.items():
        np.testing.assert_allclose(got[symbol], value, rtol=1e-12)


def test_build_symbol_stats_replaces_infinities_with_null():
    pf = make_portfolio()
    stats = build_symbol_stats(pf, run_id="r1", trades=build_trades(pf, run_id="r1"))
    numeric = stats.drop(columns=["run_id", "symbol"])
    assert not np.isinf(numeric.to_numpy(dtype="float64")).any(), "inf must be cleaned to NULL"
    # AAA has no losing trades, so vectorbt reports inf profit factor.
    assert pd.isna(stats.set_index("symbol").loc["AAA", "profit_factor"])


def test_build_symbol_stats_derives_exposure_and_trade_counts():
    pf = make_portfolio()
    trades = build_trades(pf, run_id="r1")
    stats = build_symbol_stats(pf, run_id="r1", trades=trades).set_index("symbol")
    assert stats.loc["AAA", "n_trades"] == 2
    # Two trades of 6 and 7 bars over 30 bars.
    expected = trades[trades.symbol == "AAA"]["bars_held"].sum() / len(pf.wrapper.index)
    np.testing.assert_allclose(stats.loc["AAA", "exposure"], expected, rtol=1e-12)


def test_build_equity_ungrouped_is_equal_weight_composite():
    pf = make_portfolio()
    equity, agg = build_equity(pf, run_id="r1")
    assert agg == "mean"
    assert list(equity.columns) == EQUITY_COLUMNS
    np.testing.assert_allclose(equity["value"].to_numpy(),
                               pf.value().mean(axis=1).to_numpy(), rtol=1e-12)


def test_build_equity_grouped_uses_the_portfolio_curve():
    pf = make_portfolio(grouped=True)
    equity, agg = build_equity(pf, run_id="r1")
    assert agg == "portfolio"
    np.testing.assert_allclose(equity["value"].to_numpy(),
                               np.asarray(pf.value()), rtol=1e-12)


def test_build_equity_drawdown_is_non_positive_and_starts_at_zero():
    equity, _ = build_equity(make_portfolio(), run_id="r1")
    dd = equity["drawdown"].to_numpy()
    assert dd[0] == 0
    assert (dd <= 1e-12).all()


def test_build_equity_attaches_benchmark_when_given():
    pf = make_portfolio()
    bench = pd.Series(100.0, index=pf.wrapper.index)
    equity, _ = build_equity(pf, run_id="r1", benchmark=bench)
    assert equity["benchmark_value"].notna().all()


def test_build_equity_benchmark_is_null_when_absent():
    equity, _ = build_equity(make_portfolio(), run_id="r1")
    assert equity["benchmark_value"].isna().all()
