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


from app.services.experiments.adapter import (
    FeatureCollisionError,
    attach_features,
    log_experiment,
)
from app.services.experiments.backends import LocalBackend
from app.services.experiments.store import ExperimentStore


def _features_for(pf):
    trades = build_trades(pf, run_id="r1")
    return pd.DataFrame({
        "symbol": trades["symbol"],
        "entry_dt": trades["entry_dt"],
        "rsi": np.arange(len(trades), dtype="float64"),
    })


def test_attach_features_prefixes_columns():
    pf = make_portfolio()
    out = attach_features(build_trades(pf, run_id="r1"), _features_for(pf))
    assert "feat_rsi" in out.columns
    assert "rsi" not in out.columns
    assert out["feat_rsi"].notna().all()


def test_attach_features_joins_on_symbol_and_entry_dt():
    pf = make_portfolio()
    trades = build_trades(pf, run_id="r1")
    features = _features_for(pf).iloc[[0]]  # only the first trade has a feature
    out = attach_features(trades, features)
    assert out["feat_rsi"].notna().sum() == 1
    assert len(out) == len(trades), "join must not drop trades"


def test_attach_features_rejects_a_core_column_collision():
    pf = make_portfolio()
    trades = build_trades(pf, run_id="r1")
    bad = _features_for(pf).rename(columns={"rsi": "pnl"})
    # 'pnl' would become 'feat_pnl', which is fine; a literal 'feat_' name that
    # collides with an existing trade column is the failure case.
    ok = attach_features(trades, bad)
    assert "feat_pnl" in ok.columns

    trades2 = trades.assign(feat_rsi=1.0)
    with pytest.raises(FeatureCollisionError, match="feat_rsi"):
        attach_features(trades2, _features_for(pf))


def test_attach_features_requires_join_keys():
    pf = make_portfolio()
    with pytest.raises(ValueError, match="entry_dt"):
        attach_features(build_trades(pf, run_id="r1"), pd.DataFrame({"symbol": ["AAA"]}))


def test_log_experiment_writes_a_queryable_run(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    pf = make_portfolio()
    store = ExperimentStore(backend=LocalBackend(root=tmp_path))

    handle = log_experiment(pf, name="bt 012", params={"a": 1}, tags=["oos"],
                            features=_features_for(pf), notes="hello", store=store)

    assert handle.run_id.startswith("bt-012__")
    assert handle.meta["tags"] == ["oos"]
    assert handle.meta["equity_agg"] == "mean"
    assert handle.meta["n_symbols"] == 2
    assert handle.meta["n_trades"] == 4
    assert handle.meta["metrics"]["mean_total_return"] is not None

    con = duckdb.connect(str(tmp_path / "experiments.duckdb"), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM trades").fetchone()[0] == 4
        assert con.execute("SELECT count(*) FROM symbol_stats").fetchone()[0] == 2
        assert con.execute("SELECT count(DISTINCT feat_rsi) FROM trades").fetchone()[0] == 4
    finally:
        con.close()


def test_log_experiment_records_source_metadata(tmp_path):
    pf = make_portfolio()
    store = ExperimentStore(backend=LocalBackend(root=tmp_path))
    handle = log_experiment(pf, name="bt", store=store, notebook="notebooks/backtest_012.ipynb")
    assert handle.meta["source"]["notebook"] == "notebooks/backtest_012.ipynb"
    assert "git_sha" in handle.meta["source"]


def test_log_experiment_applies_exit_reasons(tmp_path):
    pf = make_portfolio()
    trades = build_trades(pf, run_id="x")
    reasons = pd.DataFrame({
        "symbol": trades["symbol"].iloc[:1],
        "entry_dt": trades["entry_dt"].iloc[:1],
        "exit_reason": ["stop_loss"],
    })
    store = ExperimentStore(backend=LocalBackend(root=tmp_path))
    handle = log_experiment(pf, name="bt", exit_reasons=reasons, store=store)

    written = pd.read_parquet(tmp_path / "runs" / handle.run_id / "trades.parquet")
    assert written["exit_reason"].notna().sum() == 1
    assert set(written["exit_reason"].dropna()) == {"stop_loss"}


from tests.experiments_fixtures import (
    make_multiindex_portfolio,
    make_param_sweep_portfolio,
)


def test_build_trades_reads_symbol_from_multiindex_columns():
    """Parameterised runs give MultiIndex columns; the symbol is the last level.

    Stringifying the tuple instead produced "(5, 10, 'AAA')" in the store.
    """
    df = build_trades(make_multiindex_portfolio(), run_id="r1")
    assert set(df["symbol"]) <= {"AAA", "BBB"}
    assert not any("(" in s for s in df["symbol"]), df["symbol"].unique()[:3]


def test_build_symbol_stats_reads_symbol_from_multiindex_columns():
    pf = make_multiindex_portfolio()
    stats = build_symbol_stats(pf, run_id="r1", trades=build_trades(pf, run_id="r1"))
    assert sorted(stats["symbol"]) == ["AAA", "BBB"]


def test_build_trades_prefers_a_level_explicitly_named_symbol():
    """A level named 'symbol' wins over the positional last-level fallback."""
    from app.services.experiments.adapter import symbol_labels

    columns = pd.MultiIndex.from_arrays(
        [[5, 5], ["AAA", "BBB"], [10, 10]],
        names=["fast_window", "symbol", "slow_window"],
    )
    assert list(symbol_labels(columns)) == ["AAA", "BBB"]


def test_symbol_labels_falls_back_to_last_level_when_unnamed():
    from app.services.experiments.adapter import symbol_labels

    columns = pd.MultiIndex.from_arrays(
        [[5, 5], ["AAA", "BBB"]], names=["fast_window", None],
    )
    assert list(symbol_labels(columns)) == ["AAA", "BBB"]


def test_symbol_labels_passes_through_a_plain_index():
    from app.services.experiments.adapter import symbol_labels

    assert list(symbol_labels(pd.Index(["AAA", "BBB"]))) == ["AAA", "BBB"]


def test_build_trades_rejects_a_parameter_sweep():
    """Several columns per symbol cannot be reconciled with one params dict."""
    from app.services.experiments.adapter import AmbiguousSymbolColumns

    with pytest.raises(AmbiguousSymbolColumns, match="parameter"):
        build_trades(make_param_sweep_portfolio(), run_id="r1")


def test_build_symbol_stats_populates_metrics_for_multiindex_columns():
    """Metrics must carry real numbers, not NaN.

    Label-aligning vectorbt metrics against MultiIndex columns silently
    produced NaN for every metric of a real 200-symbol run, while
    trade-derived columns (n_trades, exposure) stayed populated — which is
    exactly what made it easy to miss.
    """
    pf = make_multiindex_portfolio()
    stats = build_symbol_stats(pf, run_id="r1", trades=build_trades(pf, run_id="r1"))

    for column in ["total_return", "sharpe", "max_drawdown", "win_rate"]:
        assert stats[column].notna().all(), f"{column} came back NaN: {stats[column].tolist()}"

    # And the values must match what vectorbt actually reports, per symbol.
    expected = pf.total_return()
    np.testing.assert_allclose(
        stats["total_return"].to_numpy(), np.asarray(expected, dtype="float64"), rtol=1e-12,
    )


def test_metric_values_raises_when_a_metric_cannot_be_aligned():
    from app.services.experiments.adapter import MisalignedMetric, _metric_values

    with pytest.raises(MisalignedMetric, match="sharpe"):
        _metric_values("sharpe", np.array([1.0, 2.0, 3.0]), 2)


def test_metric_values_broadcasts_a_scalar_metric():
    from app.services.experiments.adapter import _metric_values

    assert _metric_values("total_return", 0.5, 3).tolist() == [0.5, 0.5, 0.5]
