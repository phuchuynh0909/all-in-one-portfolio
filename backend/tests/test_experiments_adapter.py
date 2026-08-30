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
