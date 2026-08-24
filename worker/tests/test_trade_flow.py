"""Offline tests for the trade-flow feature SQL.

The features live only as SQL (ClickHouse computes them), so these pin the
structural properties that are easy to break silently: the MV producing exactly
the columns its target expects, the auction filter being present, and — most
importantly — that nothing order-dependent leaks into the level-1 MV, where it
would be wrong because an MV sees only one INSERT at a time.

End-to-end numeric equivalence is checked against a database by
``python workers/block_episode_ingest.py --verify``.
"""

from __future__ import annotations

from datetime import time as dtime

import pytest

from core.trade_flow import (
    FEATURE_COLUMNS,
    QTY_QUANTILES,
    SECOND_BAR_COLUMNS,
    second_bar_sql,
    window_features_sql,
)
from model import (
    TRADE_FLOW_SECONDS_CREATE_TABLE_DDL,
    TRADE_FLOW_SECONDS_MV_DDL,
    TRADE_FLOW_WINDOWS_VIEW_DDL,
)

TZ = "Asia/Ho_Chi_Minh"
WINDOWS = [(dtime(9, 0, 0), dtime(9, 15, 0)), (dtime(14, 30, 0), dtime(15, 0, 0))]


# ---------------------------------------------------------------------------
# Level 1: the materialized view body
# ---------------------------------------------------------------------------
def test_second_bar_sql_emits_exactly_the_target_columns():
    sql = second_bar_sql("db.ticks", TZ, WINDOWS)
    for col in SECOND_BAR_COLUMNS:
        assert f" AS {col}" in sql or f"    {col}," in sql, col
    # And the DDL declares every one of them.
    for col in SECOND_BAR_COLUMNS:
        assert col in TRADE_FLOW_SECONDS_CREATE_TABLE_DDL, col


def test_second_bar_sql_uses_mergeable_states_for_first_last_price():
    """min/max cannot give a bar's *first* and *last* trade; argMin/argMax can."""
    sql = second_bar_sql("db.ticks", TZ, WINDOWS)
    assert "argMinState(toFloat64(match_price), sending_time)" in sql
    assert "argMaxState(toFloat64(match_price), sending_time)" in sql


def test_second_bar_sql_keeps_quantiles_as_state():
    """Merging medians of medians is wrong; merging the states is not."""
    sql = second_bar_sql("db.ticks", TZ, WINDOWS)
    qs = ", ".join(str(q) for q in QTY_QUANTILES)
    assert f"quantilesState({qs})(toInt64(match_qty))" in sql


def test_second_bar_sql_has_no_order_dependent_constructs():
    """The core constraint: an MV sees one INSERT, so ordering is unavailable.

    Any window function, lag, or per-tick difference here would be silently
    wrong at every insert boundary — and `tick_ingest` creates one every ~2s.
    """
    sql = second_bar_sql("db.ticks", TZ, WINDOWS).lower()
    for banned in ("over (", "laginframe", "neighbor(", "rownumberinallblocks", "arraydifference"):
        assert banned not in sql, banned


def test_second_bar_sql_excludes_auctions():
    sql = second_bar_sql("db.ticks", TZ, WINDOWS)
    assert "WHERE NOT (" in sql
    assert "toTimeZone" in sql
    for bound in (32400, 33300, 52200, 54000):
        assert str(bound) in sql


def test_second_bar_sql_without_auction_windows_keeps_everything():
    sql = second_bar_sql("db.ticks", TZ, [])
    assert "WHERE NOT (0)" in sql
    assert "toTimeZone" not in sql


def test_second_bar_sql_extra_where_is_conjoined():
    sql = second_bar_sql("db.ticks", TZ, WINDOWS, extra_where="sending_time >= x")
    assert ") AND (" in sql
    assert "sending_time >= x" in sql


def test_second_bar_sql_buckets_to_one_second():
    sql = second_bar_sql("db.ticks", TZ, WINDOWS)
    assert "INTERVAL 1 SECOND" in sql
    assert "GROUP BY symbol, sec" in sql


def test_second_bar_sql_uses_real_aggressor_side():
    """The feed carries side, so no tick-rule proxy is needed."""
    sql = second_bar_sql("db.ticks", TZ, WINDOWS)
    assert "sumIf(toInt64(match_qty), side = 1)" in sql
    assert "sumIf(toInt64(match_qty), side = 2)" in sql
    assert "countIf(side = 1)" in sql


# ---------------------------------------------------------------------------
# Level 2: the window feature view
# ---------------------------------------------------------------------------
def test_window_sql_exposes_every_declared_feature():
    sql = window_features_sql("db.trade_flow_seconds", 30)
    for col in FEATURE_COLUMNS:
        assert f"AS {col}" in sql, col


def test_window_sql_merges_the_states():
    sql = window_features_sql("db.trade_flow_seconds", 30)
    assert "argMinMerge(open_px)" in sql
    assert "argMaxMerge(close_px)" in sql
    assert "quantilesMerge" in sql


def test_window_sql_derives_order_dependent_features_here_not_in_the_mv():
    """Realized vol needs ordered closes — safe at level 2, which reads a table."""
    sql = window_features_sql("db.trade_flow_seconds", 30)
    assert "arrayDifference" in sql
    assert "arraySort" in sql
    assert "realized_vol" in sql


def test_window_sql_interpolates_the_window_length():
    assert "INTERVAL 10 SECOND" in window_features_sql("db.s", 10)
    assert "INTERVAL 60 SECOND" in window_features_sql("db.s", 60)


@pytest.mark.parametrize("w", [10, 30, 60])
def test_window_sql_normalizes_rates_by_the_window(w):
    sql = window_features_sql("db.s", w)
    assert f"b.trade_count / {w}" in sql
    assert f"b.volume / {w}" in sql


def test_window_sql_guards_every_division():
    """Empty or single-price windows must yield NULL, not a division error."""
    sql = window_features_sql("db.trade_flow_seconds", 30)
    # Every '/' that divides by an aggregate is wrapped in nullIf.
    assert sql.count("nullIf(") >= 10


def test_window_sql_size_concentration_is_threshold_free():
    """No trailing per-symbol threshold is knowable in SQL, so use Σq²/V² and max/V."""
    sql = window_features_sql("db.trade_flow_seconds", 30)
    assert "b.qty_sq / nullIf(pow(toFloat64(b.volume), 2), 0)" in sql
    assert "AS size_hhi" in sql
    assert "AS top_trade_share" in sql


def test_window_sql_extra_where_reaches_both_branches():
    """base and per_sec must be filtered identically or the join drops rows."""
    sql = window_features_sql("db.s", 30, extra_where="symbol = 'HPG'")
    assert sql.count("WHERE symbol = 'HPG'") == 2


def test_ddl_templates_have_the_expected_placeholders():
    assert "{database}" in TRADE_FLOW_SECONDS_CREATE_TABLE_DDL
    assert "{table}" in TRADE_FLOW_SECONDS_CREATE_TABLE_DDL
    for key in ("{database}", "{mv}", "{table}", "{select}"):
        assert key in TRADE_FLOW_SECONDS_MV_DDL, key
    for key in ("{database}", "{view}", "{select}"):
        assert key in TRADE_FLOW_WINDOWS_VIEW_DDL, key


def test_seconds_table_is_aggregating_and_monthly_partitioned():
    ddl = TRADE_FLOW_SECONDS_CREATE_TABLE_DDL
    # Replacing would overwrite partials from separate INSERTs instead of summing.
    assert "ENGINE = AggregatingMergeTree" in ddl
    assert "ORDER BY (symbol, sec)" in ddl
    assert "PARTITION BY toYYYYMM(sec)" in ddl
    assert "SimpleAggregateFunction(sum, UInt64)" in ddl
    assert "AggregateFunction(argMin, Float64, DateTime64(6, 'UTC'))" in ddl


# ---------------------------------------------------------------------------
# Inter-arrival: recovered exactly despite the MV never seeing tick order
#
# The trick is that a millisecond offset is a property of a single tick, so
# collecting them is order-independent and the array merges by concatenation.
# Level 2 then sorts the window's offsets back into arrival order and
# differences them. Verified numerically against raw ticks by `--verify`; these
# pin the mechanism so it cannot be refactored away.
# ---------------------------------------------------------------------------
def test_second_bar_sql_collects_millisecond_offsets():
    sql = second_bar_sql("db.ticks", TZ, WINDOWS)
    assert "groupArray(toUInt16(toUnixTimestamp64Milli(sending_time) % 1000))" in sql
    assert "AS ms_offsets" in sql
    assert "ms_offsets" in SECOND_BAR_COLUMNS


def test_offsets_column_merges_by_concatenation():
    """Concatenation is what makes torn inserts recoverable."""
    assert (
        "ms_offsets SimpleAggregateFunction(groupArrayArray, Array(UInt16))"
        in TRADE_FLOW_SECONDS_CREATE_TABLE_DDL
    )


def test_window_sql_rebuilds_absolute_ms_then_sorts_before_differencing():
    sql = window_features_sql("db.trade_flow_seconds", 30)
    # absolute ms = second epoch * 1000 + offset
    assert "toInt64(toUnixTimestamp(sec)) * 1000 + o" in sql
    # The sort must be *inside* the difference — gaps taken over an unsorted
    # array are meaningless. Nesting expresses that, so assert the nesting
    # rather than textual position (the outer call appears first in the text).
    assert "arrayDifference(arraySort(arrayFlatten(" in sql
    # arrayDifference's leading 0 has no predecessor and is dropped
    assert "arraySlice(arrayDifference(arraySort" in sql


def test_window_sql_exposes_exact_interarrival_features():
    sql = window_features_sql("db.trade_flow_seconds", 30)
    for col in ("median_interarrival_ms", "p90_interarrival_ms", "same_ms_share"):
        assert f"AS {col}" in sql, col
        assert col in FEATURE_COLUMNS, col


def test_interarrival_features_guard_single_trade_windows():
    """One trade means no gap; the quantile must not be evaluated on an empty array."""
    sql = window_features_sql("db.trade_flow_seconds", 30)
    assert sql.count("if(length(p.gaps) = 0, NULL,") == 3


def test_no_p10_interarrival_feature():
    """Deliberately absent: the exchange stamps at ms and ~32% of gaps are 0,
    so a p10 percentile is pinned at zero and carries no information.
    `same_ms_share` measures the same clustering as an informative ratio."""
    assert "p10_interarrival_ms" not in FEATURE_COLUMNS
    assert "same_ms_share" in FEATURE_COLUMNS
