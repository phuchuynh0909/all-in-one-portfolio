#!/usr/bin/env python3
"""Large-order ("Layer 3") blocks — ClickHouse materialized view over `ticks`.

This replaces the former Bytewax dataflow. That flow opened its own MQTT
subscription for the whole watchlist and re-did, in Python, an aggregation
ClickHouse can do incrementally on data it already has: since `tick_ingest`
writes every watchlist symbol into `ticks`, a materialized view on that table
produces the same blocks with no second feed, no event-time watermark, and no
worker process to keep alive.

What it builds (all idempotent):

  ticks ──(MV: bucket, drop auctions, sum)──▶ large_order_blocks   AggregatingMergeTree
                                                      │
                                                      └─▶ large_orders_live  (view:
                                                          + vwap, read-time merge)

The MV carries no notional threshold. It sees one INSERT at a time, and since
`tick_ingest` flushes every ~2s while blocks are 1s wide, a bucket's fills
routinely span inserts — so each insert contributes a *partial* block. Partials
are summed by the engine; a threshold applied to a partial would drop blocks
that clear it once complete. Filter on read (`large_orders_live` exposes
`dollar_value`, as the `large_orders` table did).

This is now the *only* path. `large_orders_live` serves both history and today,
and the backend reads nothing else.

* `large_order_reconciler.py` is **retired** — no longer run, nothing schedules
  it. It and the `large_orders` (ReplacingMergeTree) table are left in place as
  a frozen archive of the 41 reconciled days (2026-05-04 → 2026-06-29) but are
  not read.
* Consequence: equity block history begins **2026-08-24**, when `tick_ingest`
  started ingesting the watchlist. The MV can only aggregate what is in
  `ticks`, and equity ticks do not exist before that date — so `--backfill`
  cannot recover it. Futures blocks go back to 2025-05-05. To rebuild equity
  history you would first have to backfill the raw ticks
  (`scripts/backfill_ticks.py`), then re-run `--backfill` here.

Usage:
    python workers/large_order_ingest.py --setup      # create table + MV + view
    python workers/large_order_ingest.py --status     # what exists, row counts
    python workers/large_order_ingest.py --backfill   # populate from existing ticks
    python workers/large_order_ingest.py --backfill --from 2026-01-01 --to 2026-08-24
    python workers/large_order_ingest.py --verify     # MV output vs core.large_order
    python workers/large_order_ingest.py --teardown   # drop MV + blocks table
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Repo root (`worker/`); needed when running this file directly.
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import clickhouse_connect  # type: ignore

from config import config
from core.large_order import (
    block_aggregation_sql,
    merge_ticks_into_blocks,
    is_auction_time,
    verify_bucket_alignment,
)
from model import (
    LARGE_ORDER_BLOCKS_CREATE_TABLE_DDL,
    LARGE_ORDER_BLOCKS_MV,
    LARGE_ORDER_BLOCKS_MV_DDL,
    LARGE_ORDER_BLOCKS_TABLE,
    LARGE_ORDERS_LIVE_VIEW,
    LARGE_ORDERS_LIVE_VIEW_DDL,
    TICKS_CLICKHOUSE_TABLE,
)

LO = config.large_order


def _client():
    c = config.clickhouse
    return clickhouse_connect.get_client(
        host=c.host,
        port=c.port,
        username=c.user,
        password=c.password,
        database=c.database,
        secure=c.secure,
        connect_timeout=c.connect_timeout,
    )


def _mv_select(source: str, extra_where: str = "") -> str:
    """The block aggregation SELECT, rendered from config."""
    return block_aggregation_sql(
        source=source,
        window_seconds=LO.window_seconds,
        tz_name=LO.session_tz,
        auction_windows=LO.auction_windows,
        extra_where=extra_where,
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def setup(cl, db: str) -> None:
    """Create the blocks table, the materialized view and the serving view."""
    # Guard first: a window that disagrees with bucket_start() would make the
    # live and reconciled paths produce different blocks.
    verify_bucket_alignment(LO.window_seconds)

    cl.command(
        LARGE_ORDER_BLOCKS_CREATE_TABLE_DDL.format(
            database=db, table=LARGE_ORDER_BLOCKS_TABLE
        )
    )
    print(f"table  {db}.{LARGE_ORDER_BLOCKS_TABLE}")

    cl.command(
        LARGE_ORDER_BLOCKS_MV_DDL.format(
            database=db,
            mv=LARGE_ORDER_BLOCKS_MV,
            table=LARGE_ORDER_BLOCKS_TABLE,
            select=_mv_select(f"{db}.{TICKS_CLICKHOUSE_TABLE}"),
        )
    )
    print(f"mv     {db}.{LARGE_ORDER_BLOCKS_MV}  (on {db}.{TICKS_CLICKHOUSE_TABLE})")

    cl.command(
        LARGE_ORDERS_LIVE_VIEW_DDL.format(
            database=db, view=LARGE_ORDERS_LIVE_VIEW, table=LARGE_ORDER_BLOCKS_TABLE
        )
    )
    print(f"view   {db}.{LARGE_ORDERS_LIVE_VIEW}")
    print(
        f"\nwindow={LO.window_seconds}s  tz={LO.session_tz}  "
        f"auctions={'excluded' if LO.auction_windows else 'kept'}\n"
        f"The MV applies no threshold — filter dollar_value on read "
        f"(LARGE_ORDER_MIN_VALUE={LO.min_dollar_value:,.0f})."
    )


def status(cl, db: str) -> None:
    def exists(name: str) -> bool:
        return bool(cl.command(f"EXISTS {db}.{name}"))

    print(f"ClickHouse {config.clickhouse.host}:{config.clickhouse.port} db={db}\n")
    for name, kind in (
        (TICKS_CLICKHOUSE_TABLE, "source"),
        (LARGE_ORDER_BLOCKS_TABLE, "target"),
        (LARGE_ORDER_BLOCKS_MV, "mv"),
        (LARGE_ORDERS_LIVE_VIEW, "view"),
        (LO.table, "reconciled"),
    ):
        if not exists(name):
            print(f"  {kind:<11} {name:<22} (missing)")
            continue
        try:
            rows = cl.command(f"SELECT count() FROM {db}.{name}")
            print(f"  {kind:<11} {name:<22} {int(rows):>14,} rows")
        except Exception:
            print(f"  {kind:<11} {name:<22} {'(no count)':>14}")

    if exists(LARGE_ORDER_BLOCKS_TABLE):
        r = cl.query(
            f"""SELECT min(sending_time), max(sending_time), uniqExact(symbol)
                FROM {db}.{LARGE_ORDER_BLOCKS_TABLE}"""
        ).result_rows[0]
        print(f"\n  blocks span : {r[0]} → {r[1]}  ({r[2]} symbols)")
        big = cl.command(
            f"""SELECT count() FROM {db}.{LARGE_ORDERS_LIVE_VIEW}
                WHERE dollar_value >= {LO.min_dollar_value}"""
        )
        print(f"  above {LO.min_dollar_value:,.0f}: {int(big):,} blocks")


def backfill(cl, db: str, day_from: date, day_to: date) -> None:
    """Populate the blocks table from ticks already stored, one day at a time.

    A materialized view only sees future inserts, so history needs this. Day
    granularity keeps each INSERT bounded and makes a re-run cheap: the day is
    dropped from the target first, so backfilling twice is not double counting.
    """
    verify_bucket_alignment(LO.window_seconds)
    day = day_from
    total = 0
    while day <= day_to:
        nxt = day + timedelta(days=1)
        # Idempotent: clear this day before reinserting it. AggregatingMergeTree
        # sums partials, so a second run would otherwise double every block.
        cl.command(
            f"""ALTER TABLE {db}.{LARGE_ORDER_BLOCKS_TABLE}
                DELETE WHERE toDate(sending_time) = '{day.isoformat()}'""",
            settings={"mutations_sync": 2},
        )
        select = _mv_select(
            f"{db}.{TICKS_CLICKHOUSE_TABLE}",
            extra_where=(
                f"sending_time >= toDateTime64('{day.isoformat()} 00:00:00', 6, 'UTC') "
                f"AND sending_time < toDateTime64('{nxt.isoformat()} 00:00:00', 6, 'UTC')"
            ),
        )
        cl.command(
            f"INSERT INTO {db}.{LARGE_ORDER_BLOCKS_TABLE} "
            f"(symbol, sending_time, side, total_qty, dollar_value, num_trades) {select}"
        )
        n = cl.command(
            f"""SELECT count() FROM {db}.{LARGE_ORDER_BLOCKS_TABLE}
                WHERE toDate(sending_time) = '{day.isoformat()}'"""
        )
        n = int(n)
        total += n
        if n:
            print(f"  {day} {n:>10,} blocks")
        day = nxt
    print(f"\nbackfilled {total:,} blocks into {db}.{LARGE_ORDER_BLOCKS_TABLE}")


def verify(cl, db: str, day: date) -> int:
    """Compare the view's blocks for *day* against core.large_order in Python.

    Returns a process exit code. This is the guard that the SQL mirror and the
    Python contract have not drifted apart.
    """
    sql_rows = cl.query(
        f"""SELECT symbol, sending_time, side, total_qty, dollar_value, num_trades
            FROM {db}.{LARGE_ORDERS_LIVE_VIEW}
            WHERE toDate(sending_time) = '{day.isoformat()}'"""
    ).result_rows
    sql_blocks = {
        (s, t, sd): (int(q), round(float(dv), 4), int(n))
        for s, t, sd, q, dv, n in sql_rows
    }

    tick_rows = cl.query(
        f"""SELECT symbol, sending_time, match_price, match_qty, side
            FROM {db}.{TICKS_CLICKHOUSE_TABLE} FINAL
            WHERE toDate(sending_time) = '{day.isoformat()}'"""
    ).result_rows
    ticks = [
        {
            "symbol": r[0],
            "sending_time": r[1],
            "match_price": r[2],
            "match_qty": r[3],
            "side": r[4],
        }
        for r in tick_rows
    ]
    kept = [
        t
        for t in ticks
        if not is_auction_time(t["sending_time"], LO.session_tz, LO.auction_windows)
    ]
    py_blocks = {
        (b["symbol"], b["sending_time"].replace(tzinfo=None), b["side"]): (
            int(b["total_qty"]),
            round(float(b["dollar_value"]), 4),
            int(b["num_trades"]),
        )
        for b in merge_ticks_into_blocks(kept, LO.window_seconds)
    }

    print(f"day {day}: {len(ticks):,} ticks ({len(ticks) - len(kept):,} auction) ")
    print(f"  python blocks : {len(py_blocks):,}")
    print(f"  view blocks   : {len(sql_blocks):,}")
    same_keys = set(py_blocks) == set(sql_blocks)
    diffs = [k for k in py_blocks if k in sql_blocks and py_blocks[k] != sql_blocks[k]]
    print(f"  keys identical: {same_keys}")
    print(f"  value diffs   : {len(diffs)}")
    for k in diffs[:5]:
        print(f"    {k} python={py_blocks[k]} view={sql_blocks[k]}")
    for k in list(set(py_blocks) - set(sql_blocks))[:5]:
        print(f"    only in python: {k} {py_blocks[k]}")
    for k in list(set(sql_blocks) - set(py_blocks))[:5]:
        print(f"    only in view  : {k} {sql_blocks[k]}")
    ok = same_keys and not diffs
    print("  RESULT        :", "MATCH" if ok else "MISMATCH")
    return 0 if ok else 1


def teardown(cl, db: str) -> None:
    cl.command(f"DROP VIEW IF EXISTS {db}.{LARGE_ORDERS_LIVE_VIEW}")
    cl.command(f"DROP VIEW IF EXISTS {db}.{LARGE_ORDER_BLOCKS_MV}")
    cl.command(f"DROP TABLE IF EXISTS {db}.{LARGE_ORDER_BLOCKS_TABLE}")
    print(
        f"dropped {LARGE_ORDERS_LIVE_VIEW}, {LARGE_ORDER_BLOCKS_MV}, "
        f"{LARGE_ORDER_BLOCKS_TABLE}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--setup", action="store_true", help="create table + MV + view")
    ap.add_argument("--status", action="store_true", help="show what exists")
    ap.add_argument("--backfill", action="store_true", help="aggregate existing ticks")
    ap.add_argument("--verify", action="store_true", help="view vs core.large_order")
    ap.add_argument("--teardown", action="store_true", help="drop MV + blocks table")
    ap.add_argument("--from", dest="day_from", help="backfill start (YYYY-MM-DD)")
    ap.add_argument("--to", dest="day_to", help="backfill end (YYYY-MM-DD)")
    ap.add_argument("--day", help="day to --verify (default: latest in ticks)")
    args = ap.parse_args()

    cl = _client()
    db = config.clickhouse.database

    if args.teardown:
        teardown(cl, db)
        return
    if args.setup:
        setup(cl, db)
    if args.backfill:
        if args.day_from and args.day_to:
            d0 = date.fromisoformat(args.day_from)
            d1 = date.fromisoformat(args.day_to)
        else:
            span = cl.query(
                f"""SELECT toDate(min(sending_time)), toDate(max(sending_time))
                    FROM {db}.{TICKS_CLICKHOUSE_TABLE}"""
            ).result_rows[0]
            d0, d1 = span[0], span[1]
            print(f"no --from/--to given; using the full tick range {d0} → {d1}")
        backfill(cl, db, d0, d1)
    if args.verify:
        day = (
            date.fromisoformat(args.day)
            if args.day
            else cl.command(
                f"SELECT toDate(max(sending_time)) FROM {db}.{TICKS_CLICKHOUSE_TABLE}"
            )
        )
        if isinstance(day, str):
            day = date.fromisoformat(day)
        raise SystemExit(verify(cl, db, day))
    if args.status or not any(
        (args.setup, args.backfill, args.verify, args.teardown)
    ):
        status(cl, db)


if __name__ == "__main__":
    main()
