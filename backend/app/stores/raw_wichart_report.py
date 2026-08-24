"""MySQL-backed store for wichart report *details*.

Two tables, one system:

  * ``raw_wichart_report`` — the crawled feed, read-only here.
  * ``wichart_reports`` — the enriched rows we write back (``llm_summary``,
    ``clean_content``, ``status`` …).

Both now live in **MySQL**. The feed used to be read from ClickHouse and the
details from a Delta table on MinIO; neither was reachable from every process
that needs them (Delta's S3 endpoint resolves to ``localhost:9000``, so the
Prefect worker running on the host could not write a summary at all). MySQL is
reachable from both the API container and the worker, so one engine serves the
join between feed and detail rows.

The engine and schema bootstrap are shared with ``report_rag_service`` via
``app.db.mysql``, so both tables are created on first use and there is no
migration step to run. Callers still get pandas DataFrames, so
``report_service`` and ``tradingagents.vn_data`` are unchanged.

Config: ``MYSQL_HOST/PORT/USER/PASSWORD/DB``, or ``MYSQL_URL`` to override the
whole DSN. See ``app/core/settings.py``.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import pandas as pd
from loguru import logger
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.engine import Engine

from app.db import mysql

_DETAIL_TABLE = os.getenv("MYSQL_WICHART_REPORT_TABLE", "wichart_reports")
_RAW_TABLE = os.getenv("MYSQL_WICHART_RAW_TABLE", "raw_wichart_report")

# Raw columns this store maps into the detail row. Kept explicit (rather than
# SELECT *) so a schema change upstream surfaces here rather than silently
# producing NULL detail fields.
_RAW_COLUMNS = (
    "id", "mack", "tenbaocao", "url", "nguon",
    "ngaykn", "rsnganh", "idnganh", "loaibaocao", "khuyennghi",
)

_metadata = MetaData()

# LONGTEXT for the generated text: a full report digest runs tens of thousands of
# characters, well past TEXT's 64 KB.
reports_table = Table(
    _DETAIL_TABLE,
    _metadata,
    Column("document_id", BigInteger, primary_key=True, autoincrement=False),
    Column("stock_symbol", String(32), index=True),
    Column("report_title", String(512)),
    Column("pdf_url", String(1024)),
    Column("source", String(128)),
    Column("report_date", DateTime, index=True),
    Column("industry_research", String(255)),
    Column("industry_id", BigInteger),
    Column("report_category", String(128)),
    Column("recommendation", String(128)),
    Column("clean_content", LONGTEXT),
    Column("llm_summary", LONGTEXT),
    Column("token_count", Integer),
    Column("status", String(32)),
    Column("error_message", Text),
    Column("created_at", DateTime),
    Column("updated_at", DateTime),
    Column("processed_at", DateTime),
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_unicode_ci",
)

# The crawled feed. Mirrors the columns the crawl workflow writes (see
# ``tasks/crawl_wichart_report_workflow.py``): the store reads a subset, but the
# table it creates on a fresh database has to be the one the crawler can fill.
# ``id`` is the primary key, so an upserting crawler dedupes on write and reads
# here need no ClickHouse-style ``FINAL`` pass.
raw_reports_table = Table(
    _RAW_TABLE,
    _metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=False),
    Column("mack", String(32), index=True),
    Column("tenbaocao", String(512)),
    Column("nguon", String(128)),
    Column("khuyennghi", String(128)),
    Column("giamuctieu", Float),
    Column("giamuctieu_dieuchinh", Float),
    Column("upside_hientai", Float),
    Column("lnst_duphong", Float),
    Column("tt_lnst_duphong_yoy", Float),
    Column("pe_mack_n0", Float),
    Column("lnst_duphong_pt", Float),
    Column("ngaykn", DateTime, index=True),
    Column("ngay_congbo", DateTime),
    Column("rsnganh", String(255)),
    Column("idnganh", BigInteger),
    Column("idnganhcap3", BigInteger),
    Column("tennganhcap3", String(255)),
    Column("kibaocao", String(64)),
    Column("loaibaocao", String(128)),
    Column("url", String(1024)),
    Column("ver", DateTime),
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_unicode_ci",
)


def _ensure_table() -> Engine:
    """Create the database and detail table if they don't exist yet (once)."""
    return mysql.ensure_table(_metadata, reports_table)


def _ensure_raw_table() -> Engine:
    """Create the database and raw feed table if absent (once).

    Read-only here, but created rather than assumed so a fresh database returns
    an empty feed instead of raising ``Table doesn't exist``.
    """
    return mysql.ensure_table(_metadata, raw_reports_table)


# ---------------------------------------------------------------------------
# Raw feed (MySQL, read-only)
# ---------------------------------------------------------------------------


def _query_raw(report_id: int | None = None, mack: str | None = None,
               limit: int | None = None) -> pd.DataFrame:
    """Read the crawled feed from MySQL into a DataFrame."""
    columns = [raw_reports_table.c[name] for name in _RAW_COLUMNS]
    stmt = select(*columns)
    if report_id is not None:
        stmt = stmt.where(raw_reports_table.c.id == int(report_id))
    if mack:
        stmt = stmt.where(raw_reports_table.c.mack == mack.upper())
    stmt = stmt.order_by(
        raw_reports_table.c.ngaykn.desc(), raw_reports_table.c.id.desc()
    )
    if limit is not None:
        stmt = stmt.limit(int(limit))

    engine = _ensure_raw_table()
    with engine.connect() as conn:
        rows = conn.execute(stmt).all()
    # Columns passed explicitly so an empty feed still has the shape callers
    # index into (``raw_df["id"]`` in ``sync_latest_reports``).
    return pd.DataFrame([tuple(r) for r in rows], columns=list(_RAW_COLUMNS))


def _detail_row_from_raw(raw: Any, now: datetime) -> dict[str, Any]:
    """Map one raw feed row onto the detail table's columns."""

    def val(key):
        v = raw.get(key)
        return None if v is None or pd.isna(v) else v

    return {
        "document_id": int(raw["id"]),
        "stock_symbol": val("mack"),
        "report_title": val("tenbaocao"),
        "pdf_url": val("url"),
        "source": val("nguon"),
        "report_date": val("ngaykn"),
        "industry_research": val("rsnganh"),
        "industry_id": val("idnganh"),
        "report_category": val("loaibaocao"),
        "recommendation": val("khuyennghi"),
        "clean_content": None,
        "llm_summary": None,
        "token_count": None,
        "status": "INIT",
        "error_message": None,
        "created_at": now,
        "updated_at": now,
        "processed_at": None,
    }


class WichartReportStore:
    """Report detail repository: reads the raw feed, owns the MySQL detail rows."""

    # -- reads ---------------------------------------------------------------

    def get_data(self, mack: str | None = None) -> pd.DataFrame:
        """The raw crawled report list, optionally filtered by ticker."""
        return _query_raw(mack=mack)

    def get_detail(self, report_id: int) -> pd.DataFrame | None:
        """Detail row for a report, creating it from the raw feed if absent.

        Returns a single-row DataFrame (callers use ``.empty`` / ``.iloc[0]``),
        or None when the report is unknown upstream too.
        """
        logger.debug(f"Getting detail for report_id={report_id}")
        try:
            engine = _ensure_table()
            with engine.connect() as conn:
                rows = conn.execute(
                    select(reports_table).where(
                        reports_table.c.document_id == int(report_id)
                    )
                ).mappings().all()
            if rows:
                return pd.DataFrame([dict(r) for r in rows])
        except Exception as exc:  # noqa: BLE001 — fall through to a create attempt
            logger.warning(
                f"Failed to query {_DETAIL_TABLE} for report_id={report_id}: {exc}"
            )

        return self._create_detail_from_raw(report_id)

    # -- writes --------------------------------------------------------------

    def _create_detail_from_raw(self, report_id: int) -> pd.DataFrame | None:
        """Insert a detail row seeded from the raw feed. None if not in the feed."""
        logger.debug(f"Creating detail from raw for report_id={report_id}")
        raw_df = _query_raw(report_id=int(report_id))
        if raw_df.empty:
            logger.warning(f"Report not found in {_RAW_TABLE}: report_id={report_id}")
            return None

        record = _detail_row_from_raw(raw_df.iloc[0], datetime.now())
        engine = _ensure_table()
        try:
            with engine.begin() as conn:
                # Insert-if-absent; a concurrent creator simply wins.
                stmt = insert(reports_table).values(**record).prefix_with("IGNORE")
                conn.execute(stmt)
            logger.info(f"Successfully created detail record for report_id={report_id}")
        except Exception as exc:
            logger.error(
                f"Failed to insert detail record for report_id={report_id}: {exc}"
            )
            raise
        return pd.DataFrame([record])

    def update_summary(self, report_id: int, summary: str) -> bool:
        """Set ``llm_summary`` for a report, creating the row if needed."""
        logger.debug(
            f"Updating summary for report_id={report_id}, "
            f"summary_length={len(summary)}"
        )
        engine = _ensure_table()
        now = datetime.now()

        with engine.begin() as conn:
            result = conn.execute(
                update(reports_table)
                .where(reports_table.c.document_id == int(report_id))
                .values(llm_summary=summary, updated_at=now)
            )
            if result.rowcount:
                logger.info(f"Successfully updated summary for report_id={report_id}")
                return True

        # No row yet — seed it from the raw feed, then set the summary.
        logger.debug(
            f"Record not found in {_DETAIL_TABLE}, creating from raw "
            f"for report_id={report_id}"
        )
        if self._create_detail_from_raw(report_id) is None:
            logger.error(f"Failed to create record from raw for report_id={report_id}")
            return False

        with engine.begin() as conn:
            result = conn.execute(
                update(reports_table)
                .where(reports_table.c.document_id == int(report_id))
                .values(llm_summary=summary, updated_at=now)
            )
        logger.info(f"Successfully updated summary for report_id={report_id}")
        return bool(result.rowcount)

    def sync_latest_reports(self, limit: int = 100) -> dict:
        """Seed detail rows for the latest raw reports that don't have one.

        Returns the same stats shape the Report page renders:
        ``total_raw / existing / missing / created / failed``.
        """
        logger.info(f"Starting sync of latest {limit} reports")
        raw_df = _query_raw(limit=limit)  # already ordered newest-first
        raw_ids = [int(i) for i in raw_df["id"].tolist()]
        logger.info(f"Found {len(raw_ids)} raw reports to check")

        engine = _ensure_table()
        with engine.connect() as conn:
            existing_total = conn.execute(
                select(func.count()).select_from(reports_table)
            ).scalar_one()
            present = set(
                conn.execute(
                    select(reports_table.c.document_id).where(
                        reports_table.c.document_id.in_(raw_ids or [0])
                    )
                ).scalars()
            )

        missing_ids = [rid for rid in raw_ids if rid not in present]
        logger.info(f"Found {len(missing_ids)} missing records to sync")

        stats = {
            "total_raw": len(raw_ids),
            "existing": int(existing_total),
            "missing": len(missing_ids),
            "created": 0,
            "failed": 0,
        }
        if not missing_ids:
            logger.info(f"Sync completed (no new records): {stats}")
            return stats

        now = datetime.now()
        missing_df = raw_df[raw_df["id"].isin(missing_ids)]
        records = [_detail_row_from_raw(row, now) for _, row in missing_df.iterrows()]
        logger.debug(f"Prepared {len(records)} records for bulk insert")

        try:
            with engine.begin() as conn:
                conn.execute(insert(reports_table).prefix_with("IGNORE"), records)
            stats["created"] = len(records)
            logger.info(f"Successfully bulk inserted {stats['created']} records")
        except Exception as exc:  # noqa: BLE001 — reported in the stats
            logger.error(f"Bulk insert failed: {exc}")
            stats["failed"] = len(missing_ids)

        logger.info(f"Sync completed: {stats}")
        return stats
