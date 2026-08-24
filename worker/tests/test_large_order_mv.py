"""Offline tests for the large-order materialized-view SQL.

The live path is a ClickHouse MV, so the block contract now exists twice: in
Python (`core.large_order`, used by the reconciler) and as SQL (used by the MV).
These tests pin the SQL mirror — that its auction predicate matches
`is_auction_time` bound for bound, that the bucket-alignment guard fires, and
that the aggregation SELECT keeps the shape the target table expects.

No network. The end-to-end equivalence against real ticks is checked by
``python workers/large_order_ingest.py --verify``, which needs a database.
"""

from __future__ import annotations

from datetime import datetime, time as dtime, timezone

import pytest

from core.large_order import (
    BLOCK_ALIGN_EPOCH,
    auction_predicate_sql,
    block_aggregation_sql,
    bucket_start,
    is_auction_time,
    merge_ticks_into_blocks,
    seconds_of_day,
    verify_bucket_alignment,
)
from model import (
    LARGE_ORDER_BLOCKS_CREATE_TABLE_DDL,
    LARGE_ORDERS_LIVE_VIEW_DDL,
)

TZ = "Asia/Ho_Chi_Minh"
ATO = (dtime(9, 0, 0), dtime(9, 15, 0))
ATC = (dtime(14, 30, 0), dtime(15, 0, 0))
WINDOWS = [ATO, ATC]


# ---------------------------------------------------------------------------
# seconds_of_day / auction predicate
# ---------------------------------------------------------------------------
def test_seconds_of_day():
    assert seconds_of_day(dtime(0, 0, 0)) == 0
    assert seconds_of_day(dtime(9, 0, 0)) == 32400
    assert seconds_of_day(dtime(9, 15, 0)) == 33300
    assert seconds_of_day(dtime(14, 30, 0)) == 52200
    assert seconds_of_day(dtime(15, 0, 0)) == 54000


def test_auction_predicate_encodes_every_window_bound():
    sql = auction_predicate_sql(WINDOWS, TZ)
    for bound in (32400, 33300, 52200, 54000):
        assert str(bound) in sql
    assert sql.count("BETWEEN") == 2
    assert TZ in sql


def test_auction_predicate_disabled_is_falsy_constant():
    """`NOT (0)` must keep every row when auction filtering is off."""
    assert auction_predicate_sql([], TZ) == "0"


def test_auction_predicate_uses_named_column():
    assert "toTimeZone(t.sending_time" in auction_predicate_sql(
        WINDOWS, TZ, column="t.sending_time"
    )


@pytest.mark.parametrize(
    "utc, expected",
    [
        # 02:00:00Z = 09:00:00 local -> inclusive lower bound of ATO
        (datetime(2026, 8, 24, 2, 0, 0, tzinfo=timezone.utc), True),
        # 02:15:00Z = 09:15:00 local -> inclusive upper bound of ATO
        (datetime(2026, 8, 24, 2, 15, 0, tzinfo=timezone.utc), True),
        # 02:15:01Z = 09:15:01 local -> just outside
        (datetime(2026, 8, 24, 2, 15, 1, tzinfo=timezone.utc), False),
        # 03:30:00Z = 10:30:00 local -> continuous trading
        (datetime(2026, 8, 24, 3, 30, 0, tzinfo=timezone.utc), False),
        # 07:45:03.9Z = 14:45:03.9 local -> inside ATC, sub-second truncated
        (datetime(2026, 8, 24, 7, 45, 3, 900000, tzinfo=timezone.utc), True),
        # 08:00:00Z = 15:00:00 local -> inclusive upper bound of ATC
        (datetime(2026, 8, 24, 8, 0, 0, tzinfo=timezone.utc), True),
        # 08:00:01Z = 15:00:01 local -> past the close
        (datetime(2026, 8, 24, 8, 0, 1, tzinfo=timezone.utc), False),
    ],
)
def test_python_auction_bounds_match_predicate_bounds(utc, expected):
    """The Python side of the mirror — SQL bounds are asserted above.

    Both are driven from the same window tuples, so a change to one bound
    without the other shows up here.
    """
    assert is_auction_time(utc, TZ, WINDOWS) is expected
    local_sod = seconds_of_day(
        utc.astimezone(__import__("zoneinfo").ZoneInfo(TZ)).replace(microsecond=0).time()
    )
    in_window = any(
        seconds_of_day(a) <= local_sod <= seconds_of_day(b) for a, b in WINDOWS
    )
    assert in_window is expected


# ---------------------------------------------------------------------------
# Bucket alignment guard
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("w", [1, 2, 5, 10, 15, 30, 60, 300])
def test_alignment_guard_accepts_sane_windows(w):
    verify_bucket_alignment(w)  # must not raise


@pytest.mark.parametrize("w", [7, 11, 13])
def test_alignment_guard_rejects_windows_that_shift_buckets(w):
    with pytest.raises(ValueError, match="does not divide"):
        verify_bucket_alignment(w)


def test_rejected_windows_really_would_disagree():
    """Proof the guard is not arbitrary: a rejected window shifts the bucket."""
    offset = int(BLOCK_ALIGN_EPOCH.timestamp())
    w = 7
    assert offset % w != 0
    ts = datetime(2026, 8, 24, 2, 0, 3, tzinfo=timezone.utc)
    python_bucket = bucket_start(ts, w)
    # ClickHouse's toStartOfInterval floors against the unix epoch instead.
    unix_floor = datetime.fromtimestamp(
        (int(ts.timestamp()) // w) * w, tz=timezone.utc
    )
    assert python_bucket != unix_floor


# ---------------------------------------------------------------------------
# Aggregation SELECT shape
# ---------------------------------------------------------------------------
def test_aggregation_sql_matches_target_columns():
    sql = block_aggregation_sql("db.ticks", 1, TZ, WINDOWS)
    ddl = LARGE_ORDER_BLOCKS_CREATE_TABLE_DDL
    for col in ("symbol", "sending_time", "side", "total_qty", "dollar_value", "num_trades"):
        assert col in sql, col
        assert col in ddl, col
    # vwap is derived on read, never stored — a ratio is not summable.
    assert "vwap" not in sql
    assert "vwap" not in ddl
    assert "vwap" in LARGE_ORDERS_LIVE_VIEW_DDL


def test_aggregation_sql_applies_no_threshold():
    """A partial block can sit below the threshold; filtering here would lose it."""
    sql = block_aggregation_sql("db.ticks", 1, TZ, WINDOWS)
    assert "HAVING" not in sql.upper()
    assert "dollar_value >=" not in sql


def test_aggregation_sql_groups_by_symbol_bucket_side():
    sql = block_aggregation_sql("db.ticks", 1, TZ, WINDOWS)
    assert "GROUP BY symbol, bucket, side" in sql
    assert "INTERVAL 1 SECOND" in sql


def test_aggregation_sql_window_seconds_is_interpolated():
    assert "INTERVAL 5 SECOND" in block_aggregation_sql("db.ticks", 5, TZ, WINDOWS)


def test_aggregation_sql_extra_where_is_conjoined():
    sql = block_aggregation_sql(
        "db.ticks", 1, TZ, WINDOWS, extra_where="sending_time >= toDateTime('2026-01-01')"
    )
    assert "2026-01-01" in sql
    assert ") AND (" in sql  # auction filter AND the range, not OR


def test_aggregation_sql_without_auctions_keeps_everything():
    sql = block_aggregation_sql("db.ticks", 1, TZ, [])
    assert "WHERE NOT (0)" in sql
    assert "toTimeZone" not in sql


# ---------------------------------------------------------------------------
# The Python contract the SQL mirrors (regression net for shared semantics)
# ---------------------------------------------------------------------------
def test_python_blocks_sum_fills_and_notional():
    ts = datetime(2026, 8, 24, 3, 0, 0, tzinfo=timezone.utc)
    ticks = [
        {"symbol": "HPG", "sending_time": ts, "match_price": 10.0, "match_qty": 100, "side": 1},
        {"symbol": "HPG", "sending_time": ts.replace(microsecond=500000),
         "match_price": 20.0, "match_qty": 100, "side": 1},
        # different side -> its own block
        {"symbol": "HPG", "sending_time": ts, "match_price": 10.0, "match_qty": 5, "side": 2},
    ]
    blocks = {(b["symbol"], b["side"]): b for b in merge_ticks_into_blocks(ticks, 1)}
    buy = blocks[("HPG", 1)]
    assert buy["num_trades"] == 2
    assert buy["total_qty"] == 200
    assert buy["dollar_value"] == pytest.approx(3000.0)
    assert buy["vwap"] == pytest.approx(15.0)
    assert blocks[("HPG", 2)]["num_trades"] == 1
