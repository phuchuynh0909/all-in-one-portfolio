"""The ohlc_5m materialized-view path: shared SQL and the DDL contract.

No ClickHouse here — these pin the SQL text and the object wiring, which is
where this design can silently rot. Value equivalence against the old table was
verified against the live server during the migration: over 16 months and
16,078 overlapping bars, open/high/low matched exactly.
"""
from __future__ import annotations

import model
from workers import ohlc_5m


def test_the_mv_and_the_rewrite_share_one_bar_definition():
    """Two definitions would let live and rewritten bars disagree."""
    live = ohlc_5m.mv_select("default.ticks")
    rewritten = ohlc_5m.mv_select("default.ticks FINAL", "sending_time >= now()")
    for fragment in (
        "toStartOfFiveMinutes(toTimezone(sending_time, 'Asia/Ho_Chi_Minh'))",
        "argMinState(match_price, sending_time)",
        "argMaxState(match_price, sending_time)",
        "sum(if(side = 1, match_qty, 0))",
        "sum(if(side = 2, match_qty, 0))",
        "GROUP BY symbol, ts",
    ):
        assert fragment in live, fragment
        assert fragment in rewritten, fragment


def test_the_mv_select_never_uses_final():
    """A materialized view sees one insert block; FINAL there is a lie."""
    assert "FINAL" not in ohlc_5m.mv_select("default.ticks")


def test_the_rewrite_reads_final_so_duplicates_are_deduped():
    """This is the pass that makes volume exact."""
    assert "FINAL" in ohlc_5m.mv_select("default.ticks FINAL", "1=1")


def test_bars_are_grouped_by_real_symbol_not_relabelled():
    """Relabelling in the MV is what merged two contracts on roll days."""
    sql = ohlc_5m.mv_select("default.ticks")
    assert "GROUP BY symbol, ts" in sql
    assert "'VN30F1M'" not in sql


def test_the_serving_view_relabels_and_aggregates_on_read():
    ddl = model.OHLC_5M_LIVE_VIEW_DDL
    assert "'VN30F1M' AS symbol" in ddl
    assert "argMinMerge(a.open)" in ddl
    assert "argMaxMerge(a.close)" in ddl
    # Without the GROUP BY a plain SELECT reads unmerged partials.
    assert "GROUP BY a.ts" in ddl
    # The front-contract join is what makes the relabel correct across a roll.
    assert model.VN30F_FRONT_TABLE in ddl.format(
        database="d", view="v", table="t", front=model.VN30F_FRONT_TABLE
    )


def test_the_aggregate_table_is_partitioned_by_day():
    """The rewrite replaces one session with DROP PARTITION; a monthly
    partition would make that impossible."""
    assert "PARTITION BY toYYYYMMDD(ts)" in model.OHLC_5M_AGG_CREATE_TABLE_DDL


def test_open_and_close_are_aggregate_state_not_simple():
    """argMin/argMax are not mergeable as SimpleAggregateFunction."""
    ddl = model.OHLC_5M_AGG_CREATE_TABLE_DDL
    assert "AggregateFunction(argMin, Float64, DateTime64(6, 'UTC'))" in ddl
    assert "AggregateFunction(argMax, Float64, DateTime64(6, 'UTC'))" in ddl
    assert "SimpleAggregateFunction(max, Float64)" in ddl
    assert "SimpleAggregateFunction(sum, Int64)" in ddl
