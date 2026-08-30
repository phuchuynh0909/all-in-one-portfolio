"""The analytical SQL shared with the frontend, run against native DuckDB.

DuckDB-WASM uses the same SQL engine, so proving these here proves the
queries the browser runs. The .sql files live under frontend/ because
TypeScript imports them with Vite's `?raw`; this test is the only reason
the backend reaches across.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")

SQL_DIR = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "experiments" / "sql"
QUANTILES = [0.10, 0.30, 0.70, 0.90]


def _read(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8")


@pytest.fixture()
def con():
    rng = np.random.default_rng(1)
    n = 200
    df = pd.DataFrame({
        "run_id": "r1",
        "trade_id": np.arange(n),
        "symbol": rng.choice(list("ABC"), n),
        "net_return": rng.normal(0.01, 0.08, n),
        "feat_rsi": rng.normal(50, 10, n),
        "feat_atr": rng.normal(2, 0.5, n),
    })
    df.loc[df.net_return < -0.08, "feat_rsi"] += 25   # inject a real signal
    df.loc[:19, "feat_atr"] = np.nan                  # 180/200 coverage
    connection = duckdb.connect()
    connection.register("trades_src", df)
    yield connection
    connection.close()


def test_outcome_buckets_partition_all_trades(con):
    out = con.execute(_read("outcome_buckets.sql"), [QUANTILES]).df()
    assert len(out) == 200
    assert set(out["outcome"]) == {
        "1_catastrophic_loss", "2_medium_loss", "3_marginal", "4_medium_win", "5_big_win",
    }
    counts = out["outcome"].value_counts()
    assert counts["1_catastrophic_loss"] == 20   # 10% quantile
    assert counts["5_big_win"] == 20             # top 10%
    assert counts["3_marginal"] == 80            # 30%-70%


def test_outcome_buckets_respect_custom_quantiles(con):
    # Collapsing each pair of cut points makes the two intermediate buckets
    # unreachable, leaving a 25/50/25 split across the outer three.
    out = con.execute(_read("outcome_buckets.sql"), [[0.25, 0.25, 0.75, 0.75]]).df()
    counts = out["outcome"].value_counts()
    assert counts.get("2_medium_loss", 0) == 0
    assert counts.get("4_medium_win", 0) == 0
    assert counts["1_catastrophic_loss"] == 50
    assert counts["3_marginal"] == 100
    assert counts["5_big_win"] == 50


def test_outcome_buckets_preserve_source_columns(con):
    out = con.execute(_read("outcome_buckets.sql"), [QUANTILES]).df()
    for column in ["run_id", "trade_id", "symbol", "net_return", "feat_rsi"]:
        assert column in out.columns


def test_feature_discrimination_ranks_the_injected_signal_first(con):
    out = con.execute(_read("feature_discrimination.sql"), [QUANTILES]).df()
    assert out.iloc[0]["feature"] == "feat_rsi"
    assert abs(out.iloc[0]["separation"]) > abs(out.iloc[1]["separation"])


def test_feature_discrimination_reports_true_coverage(con):
    out = con.execute(_read("feature_discrimination.sql"), [QUANTILES]).df().set_index("feature")
    assert out.loc["feat_rsi", "coverage"] == pytest.approx(1.0)
    # 180 of 200 trades have feat_atr. A coverage of 1.0 here means UNPIVOT
    # silently dropped the NULLs -- the bug this assertion exists to catch.
    assert out.loc["feat_atr", "coverage"] == pytest.approx(0.90)
    assert out.loc["feat_atr", "n_obs"] == 180


def test_feature_discrimination_returns_one_row_per_feature(con):
    out = con.execute(_read("feature_discrimination.sql"), [QUANTILES]).df()
    assert sorted(out["feature"]) == ["feat_atr", "feat_rsi"]
