#!/usr/bin/env python3
"""Trade-flow features — ClickHouse materialized view over `ticks`.

Replaces two things: the Bytewax `block_episode_ingest` dataflow, and the
z-score detector it ran (`core.large_execution`). `tick_ingest` already writes
every watchlist symbol into `ticks`, so the aggregation belongs in ClickHouse —
no second feed subscription, no event-time watermark, no worker to keep alive.

What it builds (all idempotent):

    ticks ──MV──▶ trade_flow_seconds    1-second bars, AggregatingMergeTree
                        │
                        └──view──▶ trade_flow_windows    N-second features

Anomaly scoring (Isolation Forest) is **not** here — it runs on demand
in the backend, which reads `trade_flow_windows` and normalizes per symbol and
time-of-day before scoring. This script only maintains the feature layer.

Why the feed shape drove the design: this is a trade/ticker tape, not
Market-By-Order. There is no resting book, no order IDs, no quotes, no
adds/cancels — so OFI, book imbalance, replenishment and queue depletion are not
computable. The features instead target size concentration, temporal
clustering, directional imbalance and price impact. See `core/trade_flow.py`
for why the levels are split and what that costs.

The old z-score detector (`core/large_execution.py`), its reconciler and the
`block_episodes` table have all been removed — this is the only path.

Usage:
    python workers/block_episode_ingest.py --setup      # table + MV + window view
    python workers/block_episode_ingest.py --status     # what exists, row counts
    python workers/block_episode_ingest.py --backfill   # bars from existing ticks
    python workers/block_episode_ingest.py --backfill --from 2026-08-01 --to 2026-08-24
    python workers/block_episode_ingest.py --verify      # MV path vs one-shot
    python workers/block_episode_ingest.py --teardown   # drop view + MV + table
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

# Repo root (`worker/`); needed when running this file directly.
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import clickhouse_connect  # type: ignore

from config import config
from core.trade_flow import (
    FEATURE_COLUMNS,
    SECOND_BAR_COLUMNS,
    second_bar_sql,
    window_features_sql,
)
from model import (
    TRADE_FLOW_SECONDS_CREATE_TABLE_DDL,
    TRADE_FLOW_SECONDS_MV,
    TRADE_FLOW_SECONDS_MV_DDL,
    TRADE_FLOW_SECONDS_TABLE,
    TRADE_FLOW_WINDOWS_VIEW,
    TRADE_FLOW_WINDOWS_VIEW_DDL,
    TICKS_CLICKHOUSE_TABLE,
)

LO = config.large_order
WINDOW_SECONDS = config.trade_flow.window_seconds


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


def _bar_select(source: str, extra_where: str = "") -> str:
    return second_bar_sql(
        source=source,
        tz_name=LO.session_tz,
        auction_windows=LO.auction_windows,
        extra_where=extra_where,
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def setup(cl, db: str) -> None:
    cl.command(
        TRADE_FLOW_SECONDS_CREATE_TABLE_DDL.format(
            database=db, table=TRADE_FLOW_SECONDS_TABLE
        )
    )
    print(f"table  {db}.{TRADE_FLOW_SECONDS_TABLE}")

    cl.command(
        TRADE_FLOW_SECONDS_MV_DDL.format(
            database=db,
            mv=TRADE_FLOW_SECONDS_MV,
            table=TRADE_FLOW_SECONDS_TABLE,
            select=_bar_select(f"{db}.{TICKS_CLICKHOUSE_TABLE}"),
        )
    )
    print(f"mv     {db}.{TRADE_FLOW_SECONDS_MV}  (on {db}.{TICKS_CLICKHOUSE_TABLE})")

    cl.command(
        TRADE_FLOW_WINDOWS_VIEW_DDL.format(
            database=db,
            view=TRADE_FLOW_WINDOWS_VIEW,
            select=window_features_sql(
                f"{db}.{TRADE_FLOW_SECONDS_TABLE}", WINDOW_SECONDS
            ),
        )
    )
    print(f"view   {db}.{TRADE_FLOW_WINDOWS_VIEW}  (window={WINDOW_SECONDS}s)")
    print(
        f"\nwindow={WINDOW_SECONDS}s  tz={LO.session_tz}  "
        f"auctions={'excluded' if LO.auction_windows else 'kept'}\n"
        f"{len(FEATURE_COLUMNS)} features per window; scoring happens in the backend.\n"
        f"Changing BLOCK_EP_WINDOW_SECONDS only needs --setup again (the view is "
        f"CREATE OR REPLACE; the 1-second bars are window-agnostic)."
    )


def status(cl, db: str) -> None:
    def exists(name: str) -> bool:
        return bool(cl.command(f"EXISTS {db}.{name}"))

    print(f"ClickHouse {config.clickhouse.host}:{config.clickhouse.port} db={db}\n")
    for name, kind in (
        (TICKS_CLICKHOUSE_TABLE, "source"),
        (TRADE_FLOW_SECONDS_TABLE, "bars"),
        (TRADE_FLOW_SECONDS_MV, "mv"),
        (TRADE_FLOW_WINDOWS_VIEW, "features"),
    ):
        if not exists(name):
            print(f"  {kind:<9} {name:<22} (missing)")
            continue
        try:
            print(
                f"  {kind:<9} {name:<22} "
                f"{int(cl.command(f'SELECT count() FROM {db}.{name}')):>14,} rows"
            )
        except Exception:
            print(f"  {kind:<9} {name:<22} {'(no count)':>14}")

    if exists(TRADE_FLOW_SECONDS_TABLE):
        r = cl.query(
            f"""SELECT min(sec), max(sec), uniqExact(symbol)
                FROM {db}.{TRADE_FLOW_SECONDS_TABLE}"""
        ).result_rows[0]
        print(f"\n  bars span : {r[0]} → {r[1]}  ({r[2]} symbols)")
        print(f"  window    : {WINDOW_SECONDS}s, {len(FEATURE_COLUMNS)} features")


def backfill(cl, db: str, day_from: date, day_to: date) -> None:
    """Build 1-second bars from ticks already stored, one day at a time.

    A materialized view only sees future inserts, so history needs this. Each
    day is cleared before reinsertion: the target sums partials, so a second run
    would otherwise double every bar.
    """
    cols = ", ".join(SECOND_BAR_COLUMNS)
    day = day_from
    total = 0
    while day <= day_to:
        nxt = day + timedelta(days=1)
        cl.command(
            f"""ALTER TABLE {db}.{TRADE_FLOW_SECONDS_TABLE}
                DELETE WHERE toDate(sec) = '{day.isoformat()}'""",
            settings={"mutations_sync": 2},
        )
        select = _bar_select(
            f"{db}.{TICKS_CLICKHOUSE_TABLE}",
            extra_where=(
                f"sending_time >= toDateTime64('{day.isoformat()} 00:00:00', 6, 'UTC') "
                f"AND sending_time < toDateTime64('{nxt.isoformat()} 00:00:00', 6, 'UTC')"
            ),
        )
        cl.command(
            f"INSERT INTO {db}.{TRADE_FLOW_SECONDS_TABLE} ({cols}) {select}"
        )
        n = int(
            cl.command(
                f"""SELECT count() FROM {db}.{TRADE_FLOW_SECONDS_TABLE}
                    WHERE toDate(sec) = '{day.isoformat()}'"""
            )
        )
        total += n
        if n:
            print(f"  {day} {n:>10,} bars")
        day = nxt
    print(f"\nbackfilled {total:,} second-bars into {db}.{TRADE_FLOW_SECONDS_TABLE}")


def verify(cl, db: str, day: date) -> int:
    """Check the incremental path against a one-shot rebuild of the same day.

    The failure mode this guards is partial-aggregate merging: the MV writes one
    row per (symbol, second) *per INSERT*, and `tick_ingest` tears seconds across
    inserts constantly. Rebuilding the day in a single INSERT gives untorn bars;
    every window feature must come out identical.
    """
    tmp_bars = f"{TRADE_FLOW_SECONDS_TABLE}__verify"
    tmp_view = f"{TRADE_FLOW_WINDOWS_VIEW}__verify"
    cl.command(f"DROP VIEW IF EXISTS {db}.{tmp_view}")
    cl.command(f"DROP TABLE IF EXISTS {db}.{tmp_bars}")
    cl.command(
        TRADE_FLOW_SECONDS_CREATE_TABLE_DDL.format(database=db, table=tmp_bars)
    )
    nxt = day + timedelta(days=1)
    cl.command(
        f"INSERT INTO {db}.{tmp_bars} ({', '.join(SECOND_BAR_COLUMNS)}) "
        + _bar_select(
            f"{db}.{TICKS_CLICKHOUSE_TABLE}",
            extra_where=(
                f"sending_time >= toDateTime64('{day.isoformat()} 00:00:00', 6, 'UTC') "
                f"AND sending_time < toDateTime64('{nxt.isoformat()} 00:00:00', 6, 'UTC')"
            ),
        )
    )
    cl.command(
        TRADE_FLOW_WINDOWS_VIEW_DDL.format(
            database=db,
            view=tmp_view,
            select=window_features_sql(f"{db}.{tmp_bars}", WINDOW_SECONDS),
        )
    )

    sel = ", ".join(("symbol", "window_start") + FEATURE_COLUMNS)
    live = cl.query(
        f"""SELECT {sel} FROM {db}.{TRADE_FLOW_WINDOWS_VIEW}
            WHERE toDate(window_start) = '{day.isoformat()}'"""
    ).result_rows
    ref = cl.query(f"SELECT {sel} FROM {db}.{tmp_view}").result_rows
    A = {(r[0], r[1]): r[2:] for r in live}
    B = {(r[0], r[1]): r[2:] for r in ref}

    parts = int(
        cl.command(
            f"""SELECT count() FROM {db}.{TRADE_FLOW_SECONDS_TABLE}
                WHERE toDate(sec) = '{day.isoformat()}'"""
        )
    )
    untorn = int(cl.command(f"SELECT count() FROM {db}.{tmp_bars}"))
    print(f"day {day}")
    print(f"  second-bar rows : live {parts:,} (partials) vs rebuilt {untorn:,}")
    print(f"  windows         : live {len(A):,} vs rebuilt {len(B):,}")
    print(f"  keys identical  : {set(A) == set(B)}")

    worst = 0.0
    worst_name = ""
    bad = 0
    for k in A.keys() & B.keys():
        for i, name in enumerate(FEATURE_COLUMNS):
            va, vb = A[k][i], B[k][i]
            if va is None or vb is None:
                bad += va != vb
                continue
            rel = abs(float(va) - float(vb)) / max(abs(float(vb)), 1e-12)
            if rel > worst:
                worst, worst_name = rel, name
            if rel > 1e-9 and abs(float(va) - float(vb)) > 1e-9:
                bad += 1
    print(f"  value diffs     : {bad}")
    print(f"  largest rel diff: {worst:.3e} ({worst_name or 'n/a'})")
    ok = set(A) == set(B) and bad == 0
    print("  RESULT          :", "MATCH" if ok else "MISMATCH")

    cl.command(f"DROP VIEW IF EXISTS {db}.{tmp_view}")
    cl.command(f"DROP TABLE IF EXISTS {db}.{tmp_bars}")
    return 0 if ok else 1


def teardown(cl, db: str) -> None:
    cl.command(f"DROP VIEW IF EXISTS {db}.{TRADE_FLOW_WINDOWS_VIEW}")
    cl.command(f"DROP VIEW IF EXISTS {db}.{TRADE_FLOW_SECONDS_MV}")
    cl.command(f"DROP TABLE IF EXISTS {db}.{TRADE_FLOW_SECONDS_TABLE}")
    print(
        f"dropped {TRADE_FLOW_WINDOWS_VIEW}, {TRADE_FLOW_SECONDS_MV}, "
        f"{TRADE_FLOW_SECONDS_TABLE}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--setup", action="store_true", help="create table + MV + view")
    ap.add_argument("--status", action="store_true", help="show what exists")
    ap.add_argument("--backfill", action="store_true", help="build bars from ticks")
    ap.add_argument("--verify", action="store_true", help="MV path vs one-shot rebuild")
    ap.add_argument("--teardown", action="store_true", help="drop view + MV + table")
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
        day = args.day or cl.command(
            f"SELECT toDate(max(sending_time)) FROM {db}.{TICKS_CLICKHOUSE_TABLE}"
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
