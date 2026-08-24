"""One-shot backfill: ClickHouse ``raw_wichart_report`` → MySQL.

The wichart report feed moved from ClickHouse to MySQL (see
``app/stores/raw_wichart_report.py``). New crawls land in MySQL on their own,
but the history that ClickHouse already holds has to be copied across once —
that is all this script does. It is idempotent (upsert on ``id``), so a partial
run can simply be repeated.

The MySQL side reuses the store's table definition and bootstrap, so this writes
exactly the table the API reads and creates it if it isn't there yet.

Usage:
    # look before you leap: counts, column mapping, a sample row — no writes
    python scripts/migrate_wichart_reports_to_mysql.py --dry-run

    # the real thing
    python scripts/migrate_wichart_reports_to_mysql.py

    # a slice, for a smoke test
    python scripts/migrate_wichart_reports_to_mysql.py --limit 50

    # non-default source (defaults come from CLICKHOUSE_* / app settings)
    python scripts/migrate_wichart_reports_to_mysql.py \\
        --ch-host 192.168.1.3 --ch-port 8123 --ch-user kyostyle1 --ch-password ...

MySQL target: ``MYSQL_HOST/PORT/USER/PASSWORD/DB`` or ``MYSQL_URL``, same as the
app. Run it with the backend ``.env`` loaded (this script loads it too).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

# The store imports live under ``backend/`` — make them importable when this is
# run as ``python scripts/…`` from anywhere.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_ROOT / ".env")

import clickhouse_connect  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.dialects.mysql import insert as mysql_insert  # noqa: E402

from app.core.settings import settings  # noqa: E402
from app.db import mysql as mysql_db  # noqa: E402
from app.stores.raw_wichart_report import (  # noqa: E402
    _ensure_raw_table,
    raw_reports_table,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--ch-host", default=settings.clickhouse_host)
    p.add_argument("--ch-port", type=int, default=int(os.getenv("CLICKHOUSE_PORT", "8123")),
                   help="ClickHouse HTTP port (8123 by default — not the native 9000/9010 one)")
    p.add_argument("--ch-user", default=settings.clickhouse_user)
    p.add_argument("--ch-password", default=settings.clickhouse_password)
    p.add_argument("--ch-db", default=settings.clickhouse_db)
    p.add_argument("--ch-table", default=os.getenv("CLICKHOUSE_WICHART_REPORT_TABLE", "raw_wichart_report"))
    p.add_argument("--batch-size", type=int, default=1000,
                   help="rows per read/upsert round-trip (default 1000)")
    p.add_argument("--limit", type=int, default=None,
                   help="stop after this many source rows (for a smoke test)")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would be copied, write nothing")
    return p.parse_args()


def _source_columns(client, database: str, table: str) -> list[str]:
    """Columns present in *both* the ClickHouse feed and the MySQL table.

    Intersected rather than assumed so an upstream schema that has drifted (a
    column added or dropped on either side) copies what it can instead of
    failing the whole run.
    """
    described = {row[0] for row in client.query(f"DESCRIBE TABLE {database}.{table}").result_rows}
    target = [c.name for c in raw_reports_table.columns]
    missing = [c for c in target if c not in described]
    if missing:
        print(f"note: not in ClickHouse, will be left NULL: {', '.join(missing)}")
    extra = sorted(described - set(target))
    if extra:
        print(f"note: in ClickHouse but not in the MySQL table, skipped: {', '.join(extra)}")
    return [c for c in target if c in described]


def _fetch_batch(client, database: str, table: str, columns: list[str],
                 after_id: int, size: int) -> list[dict[str, Any]]:
    """One keyset page of the feed, ``FINAL`` so ReplacingMergeTree is collapsed.

    Keyset (``id > last``) rather than OFFSET: stable under concurrent writes
    and it doesn't get slower as the offset grows.
    """
    query = (
        f"SELECT {', '.join(columns)} FROM {database}.{table} FINAL "
        f"WHERE id > %(after_id)s ORDER BY id LIMIT {int(size)}"
    )
    result = client.query(query, parameters={"after_id": after_id})
    return [dict(zip(result.column_names, row)) for row in result.result_rows]


def main() -> int:
    args = _parse_args()

    client = clickhouse_connect.get_client(
        host=args.ch_host,
        port=args.ch_port,
        username=args.ch_user,
        password=args.ch_password,
        database=args.ch_db,
    )
    source = f"{args.ch_host}:{args.ch_port}/{args.ch_db}.{args.ch_table}"
    target = f"{mysql_db.endpoint()}.{raw_reports_table.name}"

    try:
        total_source = client.query(
            f"SELECT count() FROM {args.ch_db}.{args.ch_table} FINAL"
        ).result_rows[0][0]
        columns = _source_columns(client, args.ch_db, args.ch_table)
        print(f"source: {source} ({total_source} rows)")
        print(f"target: {target}")
        print(f"copying {len(columns)} columns: {', '.join(columns)}")

        engine = _ensure_raw_table()
        with engine.connect() as conn:
            before = conn.execute(
                select(func.count()).select_from(raw_reports_table)
            ).scalar_one()
        print(f"target rows before: {before}")

        if args.dry_run:
            sample = _fetch_batch(client, args.ch_db, args.ch_table, columns, 0, 1)
            if sample:
                print("sample row:")
                for key, value in sample[0].items():
                    print(f"  {key}: {value!r}")
            planned = min(total_source, args.limit) if args.limit else total_source
            print(f"dry run — would upsert up to {planned} rows, nothing written")
            return 0

        stmt = mysql_insert(raw_reports_table)
        upsert = stmt.on_duplicate_key_update(
            **{c: stmt.inserted[c] for c in columns if c != "id"}
        )

        after_id, copied, skipped = 0, 0, 0
        while True:
            size = args.batch_size
            if args.limit is not None:
                size = min(size, args.limit - copied - skipped)
                if size <= 0:
                    break

            rows = _fetch_batch(client, args.ch_db, args.ch_table, columns, after_id, size)
            if not rows:
                break

            after_id = max(int(r["id"]) for r in rows)
            # ``id`` is the primary key; a feed row without one can't be keyed.
            usable = [r for r in rows if r.get("id") is not None]
            skipped += len(rows) - len(usable)
            if usable:
                with engine.begin() as conn:
                    conn.execute(upsert, usable)
                copied += len(usable)
            print(f"  {copied}/{total_source} rows (through id {after_id})")

        with engine.connect() as conn:
            after = conn.execute(
                select(func.count()).select_from(raw_reports_table)
            ).scalar_one()
        print(f"done: upserted {copied} rows"
              + (f", skipped {skipped} without an id" if skipped else ""))
        print(f"target rows: {before} → {after}")
        if copied and after < total_source and args.limit is None:
            print("warning: target has fewer rows than the source — re-run to retry")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
