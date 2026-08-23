#!/usr/bin/env python3
"""
Repartition the ClickHouse `ticks` table from daily to monthly partitions.

ClickHouse cannot ALTER a table's PARTITION BY, so switching
`toYYYYMMDD(sending_time)` → `toYYYYMM(sending_time)` means building a new
table from `model.TICKS_CREATE_TABLE_DDL`, copying the rows across, and
swapping the two names atomically with EXCHANGE TABLES.

The copy is watermarked on `received_at` so `tick_ingest` may keep running:
rows that land after the snapshot are carried over in a catch-up pass after
the swap. Overlap is harmless — ReplacingMergeTree collapses duplicates on the
ORDER BY key.

Usage:
    python scripts/migrate_ticks_partition.py                # plan only, no writes
    python scripts/migrate_ticks_partition.py --copy         # build + fill the shadow table
    python scripts/migrate_ticks_partition.py --swap         # copy, verify, then EXCHANGE
    python scripts/migrate_ticks_partition.py --drop-old     # remove the backup after verifying
    python scripts/migrate_ticks_partition.py --rollback     # swap the old table back in

After --swap the pre-migration table survives as `ticks_premigration`, so a
bad outcome is one --rollback away. Only --drop-old is irreversible.
"""

import argparse
import sys
from pathlib import Path

# Repo root (`worker/`); needed when running `python scripts/migrate_ticks_partition.py`.
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import clickhouse_connect  # type: ignore

from config import config
from model import TICKS_CLICKHOUSE_TABLE, TICKS_CREATE_TABLE_DDL

SHADOW_TABLE = "ticks_repartition"
BACKUP_TABLE = "ticks_premigration"

TICKS_COLUMNS = (
    "symbol, sending_time, match_price, match_qty, side, received_at"
)


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


def _table_stats(cl, db: str, table: str) -> dict | None:
    """Row count, part count and partition expression for a table, or None."""
    if not cl.command(f"EXISTS {db}.{table}"):
        return None
    row = cl.query(
        f"""
        SELECT partition_key, sorting_key
        FROM system.tables WHERE database = '{db}' AND name = '{table}'
        """
    ).result_rows[0]
    parts = cl.query(
        f"""
        SELECT count(), uniqExact(partition), sum(rows), sum(bytes_on_disk)
        FROM system.parts
        WHERE database = '{db}' AND table = '{table}' AND active
        """
    ).result_rows[0]
    return {
        "partition_key": row[0],
        "sorting_key": row[1],
        "parts": parts[0],
        "partitions": parts[1],
        "rows": parts[2] or 0,
        "bytes": parts[3] or 0,
    }


def _print_stats(label: str, s: dict | None) -> None:
    if s is None:
        print(f"  {label:<22} (does not exist)")
        return
    print(
        f"  {label:<22} rows={s['rows']:>12,}  parts={s['parts']:>5,}  "
        f"partitions={s['partitions']:>5,}  disk={s['bytes'] / 1048576:>8.2f} MiB"
    )
    print(f"  {'':<22} PARTITION BY {s['partition_key'] or '(none)'}")


def _dedup_count(cl, db: str, table: str) -> int:
    """Distinct ORDER BY keys — the row count after ReplacingMergeTree collapses."""
    return int(
        cl.command(
            f"""
            SELECT uniqExact((symbol, sending_time, match_price, match_qty, side))
            FROM {db}.{table}
            """
        )
    )


def plan(cl, db: str) -> None:
    print(f"\nClickHouse {config.clickhouse.host}:{config.clickhouse.port} db={db}\n")
    print("Current state:")
    _print_stats(TICKS_CLICKHOUSE_TABLE, _table_stats(cl, db, TICKS_CLICKHOUSE_TABLE))
    _print_stats(SHADOW_TABLE, _table_stats(cl, db, SHADOW_TABLE))
    _print_stats(BACKUP_TABLE, _table_stats(cl, db, BACKUP_TABLE))
    print("\nTarget PARTITION BY toYYYYMM(sending_time)")
    print("Run with --copy to build the shadow table, then --swap.\n")


def copy(cl, db: str) -> tuple[int, str]:
    """Create the shadow table and copy every row at or below a watermark."""
    src = _table_stats(cl, db, TICKS_CLICKHOUSE_TABLE)
    if src is None:
        raise SystemExit(f"{db}.{TICKS_CLICKHOUSE_TABLE} does not exist — nothing to migrate")

    cl.command(f"DROP TABLE IF EXISTS {db}.{SHADOW_TABLE}")
    cl.command(TICKS_CREATE_TABLE_DDL.format(database=db, table=SHADOW_TABLE))
    print(f"Created {db}.{SHADOW_TABLE}")

    shadow = _table_stats(cl, db, SHADOW_TABLE)
    if shadow is None:
        raise SystemExit(f"{db}.{SHADOW_TABLE} was not created — check TICKS_CREATE_TABLE_DDL")
    if "toYYYYMM" not in (shadow["partition_key"] or ""):
        raise SystemExit(
            f"shadow table partition key is {shadow['partition_key']!r}, expected toYYYYMM — "
            "check TICKS_CREATE_TABLE_DDL"
        )

    watermark = cl.command(
        f"SELECT toString(max(received_at)) FROM {db}.{TICKS_CLICKHOUSE_TABLE}"
    )
    print(f"Watermark received_at <= {watermark}")
    print(f"Copying ~{src['rows']:,} rows ...")
    cl.command(
        f"""
        INSERT INTO {db}.{SHADOW_TABLE} ({TICKS_COLUMNS})
        SELECT {TICKS_COLUMNS} FROM {db}.{TICKS_CLICKHOUSE_TABLE}
        WHERE received_at <= toDateTime64('{watermark}', 6, 'UTC')
        """
    )
    copied = _table_stats(cl, db, SHADOW_TABLE)["rows"]
    print(f"Copied. shadow rows={copied:,}")
    return copied, watermark


def verify(cl, db: str, watermark: str) -> None:
    """Compare de-duplicated row counts on both sides of the watermark."""
    src = _dedup_count(cl, db, TICKS_CLICKHOUSE_TABLE)
    dst = _dedup_count(cl, db, SHADOW_TABLE)
    tail = int(
        cl.command(
            f"""
            SELECT count() FROM {db}.{TICKS_CLICKHOUSE_TABLE}
            WHERE received_at > toDateTime64('{watermark}', 6, 'UTC')
            """
        )
    )
    print(f"Distinct keys: source={src:,} shadow={dst:,} (source tail after watermark={tail:,})")
    if dst + tail < src:
        raise SystemExit(
            f"verification FAILED — shadow is short by {src - dst - tail:,} distinct keys; "
            "shadow table left in place, nothing swapped"
        )
    print("Verification OK")


def swap(cl, db: str, watermark: str) -> None:
    """Atomically exchange the tables, then carry over post-watermark rows."""
    cl.command(f"DROP TABLE IF EXISTS {db}.{BACKUP_TABLE}")
    cl.command(
        f"EXCHANGE TABLES {db}.{TICKS_CLICKHOUSE_TABLE} AND {db}.{SHADOW_TABLE}"
    )
    cl.command(f"RENAME TABLE {db}.{SHADOW_TABLE} TO {db}.{BACKUP_TABLE}")
    print(
        f"Swapped. {TICKS_CLICKHOUSE_TABLE} is now monthly-partitioned; "
        f"previous table kept as {BACKUP_TABLE}"
    )

    print("Catch-up pass for rows written during the copy ...")
    cl.command(
        f"""
        INSERT INTO {db}.{TICKS_CLICKHOUSE_TABLE} ({TICKS_COLUMNS})
        SELECT {TICKS_COLUMNS} FROM {db}.{BACKUP_TABLE}
        WHERE received_at > toDateTime64('{watermark}', 6, 'UTC')
        """
    )
    _print_stats(
        TICKS_CLICKHOUSE_TABLE, _table_stats(cl, db, TICKS_CLICKHOUSE_TABLE)
    )


def rollback(cl, db: str) -> None:
    if not cl.command(f"EXISTS {db}.{BACKUP_TABLE}"):
        raise SystemExit(f"{db}.{BACKUP_TABLE} not found — nothing to roll back to")
    cl.command(
        f"EXCHANGE TABLES {db}.{TICKS_CLICKHOUSE_TABLE} AND {db}.{BACKUP_TABLE}"
    )
    print(f"Rolled back. {TICKS_CLICKHOUSE_TABLE} is the pre-migration table again.")
    _print_stats(
        TICKS_CLICKHOUSE_TABLE, _table_stats(cl, db, TICKS_CLICKHOUSE_TABLE)
    )


def drop_old(cl, db: str) -> None:
    s = _table_stats(cl, db, BACKUP_TABLE)
    if s is None:
        print(f"{db}.{BACKUP_TABLE} does not exist — nothing to drop")
        return
    cl.command(f"DROP TABLE {db}.{BACKUP_TABLE}")
    print(f"Dropped {db}.{BACKUP_TABLE} ({s['rows']:,} rows) — migration is now final.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--copy", action="store_true", help="build + fill the shadow table")
    ap.add_argument("--swap", action="store_true", help="copy, verify, then EXCHANGE TABLES")
    ap.add_argument("--rollback", action="store_true", help="restore the pre-migration table")
    ap.add_argument("--drop-old", action="store_true", help="drop the pre-migration backup")
    args = ap.parse_args()

    cl = _client()
    db = config.clickhouse.database

    if args.rollback:
        rollback(cl, db)
    elif args.drop_old:
        drop_old(cl, db)
    elif args.swap:
        _, watermark = copy(cl, db)
        verify(cl, db, watermark)
        swap(cl, db, watermark)
    elif args.copy:
        _, watermark = copy(cl, db)
        verify(cl, db, watermark)
        print("\nShadow table ready. Re-run with --swap to cut over.\n")
    else:
        plan(cl, db)


if __name__ == "__main__":
    main()
