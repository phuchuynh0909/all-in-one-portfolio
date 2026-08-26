"""One-shot copy: the SQLite ``portfolio.db`` → MySQL ``my_portfolio``.

The app's ORM moved off the single-file SQLite database onto MySQL, alongside
the wichart report store that already lived there (see ``app/db/base.py`` and
``app/core/settings.py``). Alembic builds the MySQL schema — revision
``c4d8e1f60b93``, the squashed baseline::

    alembic stamp a1b2c3d4e5f6   # skip the un-replayable SQLite-era history
    alembic upgrade head

This script moves the *rows*. Tables are copied in foreign-key-safe order, with
their primary keys preserved so existing IDs keep meaning, and each row is
upserted on its primary key — so a partial or repeated run is harmless.

Two write modes, and the difference matters:

``--replace`` makes MySQL's contents *equal* the source: every row in the eleven
tables is deleted first, then reloaded. This is the right mode when the SQLite
file is the authority — notably because primary keys are not stable across
snapshots of it. A ``company_id`` that named one ticker in an older export can
name a different one in a newer export, and since ``company.ticker`` carries a
UNIQUE index, upserting by key then collides; worse, ``item_value`` rows
reference the *source's* ``company_id``, so a half-merged table silently
misattributes financial data to the wrong company.

The default (upsert) mode only adds and updates, leaving unmatched target rows
in place. Use it to top up a target that shares the source's key space.

Usage:
    # look before you leap: per-table row counts, source and target — no writes
    python scripts/migrate_portfolio_db_to_mysql.py --dry-run

    # make MySQL match the file exactly (recommended when the file is the truth)
    python scripts/migrate_portfolio_db_to_mysql.py --sqlite ../portfolio.db --replace

    # upsert only, leaving other target rows untouched
    python scripts/migrate_portfolio_db_to_mysql.py

    # verify only: compare row counts, copy nothing
    python scripts/migrate_portfolio_db_to_mysql.py --verify

MySQL target: ``MYSQL_HOST/PORT/USER/PASSWORD/DB`` or ``MYSQL_URL``, same as the
app. Run it with the backend ``.env`` loaded (this script loads it too).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

# The app imports live under ``backend/`` — make them importable when this is
# run as ``python scripts/…`` from anywhere.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_ROOT / ".env")

from sqlalchemy import create_engine, text  # noqa: E402

from app.core.settings import settings  # noqa: E402


# Parents before children: ``statement_item`` references ``statement`` and
# itself, ``item_value`` references all three of its parents. Copying in this
# order means no foreign key is ever dangling mid-run.
TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('positions', ('id',)),
    ('transactions', ('id',)),
    ('investment_amounts', ('id',)),
    ('price_alerts', ('id',)),
    ('sector', ('id', 'level')),
    ('stock_symbol', ('id',)),
    ('company', ('company_id',)),
    ('period', ('period_id',)),
    ('statement', ('statement_id',)),
    ('statement_item', ('item_id',)),
    ('item_value', ('item_value_id',)),
)

BATCH = 500


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _mysql_columns(mysql_conn, table: str) -> set[str]:
    rows = mysql_conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t"
        ),
        {"t": table},
    )
    return {r[0] for r in rows}


def _batched(rows: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def _upsert_sql(table: str, columns: list[str], keys: tuple[str, ...]) -> str:
    """``INSERT … ON DUPLICATE KEY UPDATE`` over the non-key columns.

    MySQL's upsert, not SQLite/Postgres' ``ON CONFLICT``. With no non-key column
    to update (a pure key table) the clause still needs a no-op assignment, so
    it re-assigns the first key to itself.
    """
    cols = ", ".join(f"`{c}`" for c in columns)
    params = ", ".join(f":{c}" for c in columns)
    updatable = [c for c in columns if c not in keys]
    if updatable:
        updates = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in updatable)
    else:
        updates = f"`{keys[0]}` = VALUES(`{keys[0]}`)"
    return f"INSERT INTO `{table}` ({cols}) VALUES ({params}) ON DUPLICATE KEY UPDATE {updates}"


def copy_table(
    sqlite_conn: sqlite3.Connection,
    mysql_conn,
    table: str,
    keys: tuple[str, ...],
    dry_run: bool,
) -> tuple[int, int]:
    """Copy one table. Returns (rows in source, rows written)."""
    src_cols = _sqlite_columns(sqlite_conn, table)
    if not src_cols:
        print(f"  {table:<20} SKIP — not present in SQLite source")
        return (0, 0)

    dst_cols = _mysql_columns(mysql_conn, table)
    missing = dst_cols - set(src_cols)
    extra = set(src_cols) - dst_cols
    columns = [c for c in src_cols if c in dst_cols]
    if extra:
        print(f"  {table:<20} note: SQLite-only columns ignored: {sorted(extra)}")
    if missing:
        print(f"  {table:<20} note: MySQL-only columns left at default: {sorted(missing)}")

    rows = sqlite_conn.execute(
        f"SELECT {', '.join(columns)} FROM {table}"
    ).fetchall()
    if dry_run:
        print(f"  {table:<20} {len(rows):>6} rows would be copied ({len(columns)} cols)")
        return (len(rows), 0)

    if not rows:
        print(f"  {table:<20} {0:>6} rows (empty)")
        return (0, 0)

    stmt = text(_upsert_sql(table, columns, keys))
    written = 0
    for batch in _batched(rows, BATCH):
        mysql_conn.execute(stmt, [dict(zip(columns, r)) for r in batch])
        written += len(batch)
    print(f"  {table:<20} {written:>6} rows copied")
    return (len(rows), written)


def clear_targets(mysql_conn, dry_run: bool) -> None:
    """Empty the eleven tables, children before parents.

    ``DELETE`` rather than ``TRUNCATE``: truncate commits implicitly in MySQL, so
    it would break the all-or-nothing guarantee this script relies on, and it
    refuses to run on a table other tables reference. The leftover
    ``AUTO_INCREMENT`` counter is harmless — rows are reloaded with explicit
    primary keys.
    """
    for table, _ in reversed(TABLES):
        if dry_run:
            count = mysql_conn.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar()
            print(f"  {table:<20} {count:>6} existing rows would be deleted")
            continue
        result = mysql_conn.execute(text(f"DELETE FROM `{table}`"))
        if result.rowcount:
            print(f"  {table:<20} {result.rowcount:>6} rows deleted")


def verify(sqlite_conn: sqlite3.Connection, mysql_conn) -> bool:
    """Compare per-table row counts between source and target."""
    ok = True
    print("\nVerification (source → target row counts):")
    for table, _ in TABLES:
        if not _sqlite_columns(sqlite_conn, table):
            continue
        src = sqlite_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        dst = mysql_conn.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar()
        flag = "ok" if src == dst else "MISMATCH"
        if src != dst:
            ok = False
        print(f"  {table:<20} {src:>6} → {dst:>6}  {flag}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sqlite",
        default=str(BACKEND_ROOT / "app" / "portfolio.db"),
        help="SQLite source file (default: app/portfolio.db)",
    )
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    parser.add_argument("--verify", action="store_true", help="compare counts only, copy nothing")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="delete all target rows first so MySQL ends up equal to the source",
    )
    args = parser.parse_args()

    src_path = Path(args.sqlite)
    if not src_path.exists():
        print(f"SQLite source not found: {src_path}", file=sys.stderr)
        return 1

    target = settings.mysql_url.split("@", 1)[-1]
    print(f"source: {src_path}")
    print(f"target: mysql://…@{target}\n")

    sqlite_conn = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    engine = create_engine(settings.mysql_url, future=True)

    try:
        with engine.begin() as mysql_conn:
            if args.verify:
                return 0 if verify(sqlite_conn, mysql_conn) else 1

            mode = "replace" if args.replace else "upsert"
            print(f"mode:   {mode}\n")

            # Children are inserted after parents, but a repeated run can still
            # touch rows in any order — defer FK checks for the transaction so a
            # re-run cannot trip over a constraint it already satisfies. Also
            # what lets --replace delete parents without fighting the children.
            mysql_conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))

            if args.replace:
                print("Clearing:" if not args.dry_run else "Dry run (clear):")
                clear_targets(mysql_conn, args.dry_run)
                print()

            print("Copying:" if not args.dry_run else "Dry run:")
            total = 0
            for table, keys in TABLES:
                _, written = copy_table(sqlite_conn, mysql_conn, table, keys, args.dry_run)
                total += written
            mysql_conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
            print(f"\n{total} rows written")

            if not args.dry_run:
                if not verify(sqlite_conn, mysql_conn):
                    print("\nRow counts do not match — not committing.", file=sys.stderr)
                    raise SystemExit(1)
    finally:
        sqlite_conn.close()
        engine.dispose()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
