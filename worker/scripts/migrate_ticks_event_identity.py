#!/usr/bin/env python3
"""Migrate ``ticks`` to replay-safe DNSE event identity.

Stop the source worker during ``--swap``. The migration copies ``ticks FINAL``
into a shadow table, preserving existing event sequences or assigning legacy
sequences when the source lacks them. It verifies count and volume, recreates
dependent materialized views, and atomically exchanges tables. The prior table
remains available for rollback.

Usage:
    python scripts/migrate_ticks_event_identity.py
    python scripts/migrate_ticks_event_identity.py --copy --confirm-ingest-stopped
    python scripts/migrate_ticks_event_identity.py --swap --confirm-ingest-stopped
    python scripts/migrate_ticks_event_identity.py --rollback --confirm-ingest-stopped
    python scripts/migrate_ticks_event_identity.py --drop-old
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import clickhouse_connect  # type: ignore

from config import config
from model import (
    LEGACY_EVENT_SEQUENCE_START,
    LARGE_ORDER_BLOCKS_MV,
    OHLC_5M_MV,
    TICKS_CLICKHOUSE_ORDER_BY,
    TICKS_CLICKHOUSE_TABLE,
    TICKS_CREATE_TABLE_DDL,
    TRADE_FLOW_SECONDS_MV,
)

SHADOW_TABLE = "ticks_event_identity"
BACKUP_TABLE = "ticks_pre_timestamp_identity"
BASE_COLUMNS = (
    "symbol, sending_time, match_price, match_qty, side, received_at, board_id"
)
TARGET_COLUMNS = f"{BASE_COLUMNS}, event_sequence"


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


def _exists(cl, db: str, table: str) -> bool:
    return bool(cl.command(f"EXISTS {db}.{table}"))

def _has_column(cl, db: str, table: str, column: str) -> bool:
    return bool(
        cl.command(
            "SELECT count() FROM system.columns "
            f"WHERE database = '{db}' AND table = '{table}' AND name = '{column}'"
        )
    )


def _stats(cl, db: str, table: str) -> tuple[int, int, int, str] | None:
    if not _exists(cl, db, table):
        return None
    sorting_key = cl.query(
        "SELECT sorting_key FROM system.tables "
        f"WHERE database = '{db}' AND name = '{table}'"
    ).result_rows[0][0]
    rows, quantity, parts = cl.query(
        f"SELECT count(), sum(match_qty), uniqExact(_part) FROM {db}.{table} FINAL"
    ).result_rows[0]
    return int(rows), int(quantity or 0), int(parts), str(sorting_key)


def _print_stats(label: str, stats: tuple[int, int, int, str] | None) -> None:
    if stats is None:
        print(f"  {label:<28} missing")
        return
    rows, quantity, parts, sorting_key = stats
    print(
        f"  {label:<28} rows={rows:>12,} qty={quantity:>14,} "
        f"parts={parts:>5,}\n  {'':<28} ORDER BY ({sorting_key})"
    )


def _existing_dependent_views(cl, db: str) -> tuple[str, ...]:
    names = (OHLC_5M_MV, LARGE_ORDER_BLOCKS_MV, TRADE_FLOW_SECONDS_MV)
    return tuple(name for name in names if _exists(cl, db, name))


def _drop_dependent_views(cl, db: str, names: tuple[str, ...]) -> None:
    for name in names:
        cl.command(f"DROP VIEW {db}.{name}")
        print(f"Dropped dependent materialized view {db}.{name}")


def _recreate_dependent_views(cl, db: str, names: tuple[str, ...]) -> None:
    if OHLC_5M_MV in names:
        from workers.ohlc_5m import setup

        setup(cl, db)
    if LARGE_ORDER_BLOCKS_MV in names:
        from workers.large_order_ingest import setup

        setup(cl, db)
    if TRADE_FLOW_SECONDS_MV in names:
        from workers.block_episode_ingest import setup

        setup(cl, db)


def _require_stopped(confirmed: bool) -> None:
    if not confirmed:
        raise SystemExit(
            "Stop worker-tick-ingest, then repeat with --confirm-ingest-stopped. "
            "Writes during copy/swap cannot cross two schemas safely."
        )


def plan(cl, db: str) -> None:
    print(f"\nClickHouse {config.clickhouse.host}:{config.clickhouse.port} db={db}")
    _print_stats(TICKS_CLICKHOUSE_TABLE, _stats(cl, db, TICKS_CLICKHOUSE_TABLE))
    _print_stats(SHADOW_TABLE, _stats(cl, db, SHADOW_TABLE))
    _print_stats(BACKUP_TABLE, _stats(cl, db, BACKUP_TABLE))
    print(f"\nTarget ORDER BY ({TICKS_CLICKHOUSE_ORDER_BY})")
    print("No writes performed.\n")


def copy(cl, db: str) -> None:
    source = _stats(cl, db, TICKS_CLICKHOUSE_TABLE)
    if source is None:
        raise SystemExit(f"{db}.{TICKS_CLICKHOUSE_TABLE} does not exist")
    if source[3].replace(" ", "") == TICKS_CLICKHOUSE_ORDER_BY.replace(" ", ""):
        raise SystemExit(f"{db}.{TICKS_CLICKHOUSE_TABLE} already has target identity")

    cl.command(f"DROP TABLE IF EXISTS {db}.{SHADOW_TABLE}")
    cl.command(TICKS_CREATE_TABLE_DDL.format(database=db, table=SHADOW_TABLE))
    if _has_column(cl, db, TICKS_CLICKHOUSE_TABLE, "event_sequence"):
        sequence_select = "event_sequence"
    else:
        sequence_select = (
            f"toUInt64({LEGACY_EVENT_SEQUENCE_START}) + row_number() OVER ("
            "PARTITION BY symbol, toDate(sending_time), board_id "
            "ORDER BY sending_time, match_price, match_qty, side, received_at)"
        )
    cl.command(
        f"""
        INSERT INTO {db}.{SHADOW_TABLE} ({TARGET_COLUMNS})
        SELECT {BASE_COLUMNS}, {sequence_select} AS event_sequence
        FROM {db}.{TICKS_CLICKHOUSE_TABLE} FINAL
        """
    )
    verify(cl, db)


def verify(cl, db: str) -> None:
    source = _stats(cl, db, TICKS_CLICKHOUSE_TABLE)
    shadow = _stats(cl, db, SHADOW_TABLE)
    if source is None or shadow is None:
        raise SystemExit("source or shadow table is missing")
    if shadow[3].replace(" ", "") != TICKS_CLICKHOUSE_ORDER_BY.replace(" ", ""):
        raise SystemExit(f"shadow sorting key is wrong: {shadow[3]!r}")
    if source[:2] != shadow[:2]:
        raise SystemExit(
            "verification failed: source/shadow count or quantity differs "
            f"({source[:2]} != {shadow[:2]})"
        )
    print(
        f"Verified shadow: {shadow[0]:,} rows and {shadow[1]:,} quantity preserved"
    )


def swap(cl, db: str) -> None:
    if _exists(cl, db, SHADOW_TABLE):
        verify(cl, db)
    else:
        copy(cl, db)
    dependents = _existing_dependent_views(cl, db)
    _drop_dependent_views(cl, db, dependents)
    cl.command(f"DROP TABLE IF EXISTS {db}.{BACKUP_TABLE}")
    cl.command(
        f"EXCHANGE TABLES {db}.{TICKS_CLICKHOUSE_TABLE} AND {db}.{SHADOW_TABLE}"
    )
    cl.command(f"RENAME TABLE {db}.{SHADOW_TABLE} TO {db}.{BACKUP_TABLE}")
    _recreate_dependent_views(cl, db, dependents)
    print(
        f"Swapped {db}.{TICKS_CLICKHOUSE_TABLE}; old table retained as "
        f"{db}.{BACKUP_TABLE}"
    )


def rollback(cl, db: str) -> None:
    if not _exists(cl, db, BACKUP_TABLE):
        raise SystemExit(f"{db}.{BACKUP_TABLE} does not exist")
    dependents = _existing_dependent_views(cl, db)
    _drop_dependent_views(cl, db, dependents)
    cl.command(
        f"EXCHANGE TABLES {db}.{TICKS_CLICKHOUSE_TABLE} AND {db}.{BACKUP_TABLE}"
    )
    _recreate_dependent_views(cl, db, dependents)
    print(f"Rolled back {db}.{TICKS_CLICKHOUSE_TABLE}")


def drop_old(cl, db: str) -> None:
    if not _exists(cl, db, BACKUP_TABLE):
        print(f"{db}.{BACKUP_TABLE} does not exist")
        return
    cl.command(f"DROP TABLE {db}.{BACKUP_TABLE}")
    print(f"Dropped {db}.{BACKUP_TABLE}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--copy", action="store_true")
    action.add_argument("--swap", action="store_true")
    action.add_argument("--rollback", action="store_true")
    action.add_argument("--drop-old", action="store_true")
    parser.add_argument("--confirm-ingest-stopped", action="store_true")
    args = parser.parse_args()

    cl = _client()
    db = config.clickhouse.database
    if args.copy:
        _require_stopped(args.confirm_ingest_stopped)
        copy(cl, db)
    elif args.swap:
        _require_stopped(args.confirm_ingest_stopped)
        swap(cl, db)
    elif args.rollback:
        _require_stopped(args.confirm_ingest_stopped)
        rollback(cl, db)
    elif args.drop_old:
        drop_old(cl, db)
    else:
        plan(cl, db)


if __name__ == "__main__":
    main()
