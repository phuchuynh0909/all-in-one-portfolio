"""Copy saved TradingAgents analyses from ClickHouse into MySQL.

One-shot backfill for the store move in
``app/services/tradingagents/store.py``. The new MySQL table starts empty, so
without this the history behind the Trading Agents page (and the prior-decision
context a run feeds itself) disappears.

Idempotent: rows already present in MySQL are skipped by id, so it is safe to
re-run after a partial copy. Reads are batched so a large table does not have
to fit in memory.

Usage:
    # see what would move, touch nothing
    python tasks/migrate_trading_agents_to_mysql.py --dry-run

    # do it
    python tasks/migrate_trading_agents_to_mysql.py

    # re-copy rows that already exist in MySQL (overwrite)
    python tasks/migrate_trading_agents_to_mysql.py --overwrite
"""
from __future__ import annotations

import argparse
import os
import sys

# Run from the repo root or from backend/ — both put `app` on the path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import clickhouse_connect  # noqa: E402
from loguru import logger  # noqa: E402
from sqlalchemy import delete, insert, select  # noqa: E402

from app.core.settings import settings  # noqa: E402
from app.db import mysql  # noqa: E402
from app.services.tradingagents import store  # noqa: E402

# The table the analyses used to live in, before the move.
_CH_TABLE = os.getenv("CLICKHOUSE_TRADING_AGENTS_TABLE", "trading_agent_analyses")

_COLUMNS = (
    "id", "symbol", "trade_date", "provider", "model", "signal",
    "analysts", "sections", "final_decision", "duration_ms", "created_at",
)


def _clickhouse():
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_db,
    )


def _existing_ids(engine) -> set[str]:
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(select(store._analyses.c.id)).all()}


def migrate(*, dry_run: bool = False, overwrite: bool = False, batch_size: int = 200) -> int:
    """Copy every ClickHouse row into MySQL. Returns the number written."""
    engine = mysql.ensure_table(store._metadata, store._analyses)
    already = _existing_ids(engine)
    logger.info("MySQL already holds {} analyses", len(already))

    client = _clickhouse()
    try:
        source = f"{settings.clickhouse_db}.{_CH_TABLE}"
        total = client.query(f"SELECT count() FROM {source}").result_rows[0][0]
        logger.info("ClickHouse holds {} analyses in {}", total, source)

        written = 0
        skipped = 0
        for offset in range(0, total, batch_size):
            result = client.query(
                f"SELECT {', '.join(_COLUMNS)} FROM {source} "
                f"ORDER BY created_at LIMIT {batch_size} OFFSET {offset}"
            )
            rows = []
            for r in result.result_rows:
                record = dict(zip(_COLUMNS, r))
                if record["id"] in already and not overwrite:
                    skipped += 1
                    continue
                # ClickHouse stored analysts/sections as JSON text already, so
                # they cross over untouched; created_at arrives as a datetime.
                rows.append(record)

            if not rows or dry_run:
                continue

            with engine.begin() as conn:
                if overwrite:
                    conn.execute(
                        delete(store._analyses).where(
                            store._analyses.c.id.in_([r["id"] for r in rows])
                        )
                    )
                conn.execute(insert(store._analyses), rows)
            written += len(rows)
            logger.info("… {}/{}", min(offset + batch_size, total), total)

        verb = "would copy" if dry_run else "copied"
        logger.info("{} {} analyses ({} already present)", verb, written, skipped)
        return written
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace rows whose id already exists in MySQL",
    )
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()

    migrate(dry_run=args.dry_run, overwrite=args.overwrite, batch_size=args.batch_size)


if __name__ == "__main__":
    main()
