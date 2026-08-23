"""MySQL-backed status + parsed-markdown store for the report RAG pipeline.

One row per report tracks the RAG lifecycle and holds the markdown produced by
parsing the PDF. The Prefect flow (``tasks/rag_pipeline.py``) drives the status
transitions; the API reads them so the Report page can show which reports are
already embedded.

Status lifecycle:
    PENDING -> PARSING -> PARSED -> SUMMARIZING -> EMBEDDING -> EMBEDDED
    (SUMMARIZING is skipped when RAG_SUMMARY is off; any step may transition to
    FAILED, where ``error`` holds the reason)

Table: ``report_rag`` in MySQL, keyed by ``report_id``. Every write is an
``INSERT … ON DUPLICATE KEY UPDATE``, so a row is created if missing and patched
if present — a status write can never be lost, and reads see it immediately.

This replaces a ClickHouse implementation whose complexity was almost entirely
ReplacingMergeTree bookkeeping: ``FINAL`` on every read, a three-way
upsert/save/update split to stop lightweight UPDATEs from resurrecting older
versions, an ``enable_block_number_column`` migration, the experimental-update
setting, and an INSERT fallback for markdown that blew past ``max_query_size``
when parameters were inlined into the SQL text. None of that is needed here:
values are bound, and ``markdown`` is a LONGTEXT column.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

from loguru import logger
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    select,
)
from sqlalchemy.dialects.mysql import LONGTEXT, insert as mysql_insert

from app.db import mysql

_TABLE = os.getenv("MYSQL_REPORT_RAG_TABLE", "report_rag")

# Status constants
PENDING = "PENDING"
PARSING = "PARSING"
PARSED = "PARSED"
SUMMARIZING = "SUMMARIZING"
EMBEDDING = "EMBEDDING"
EMBEDDED = "EMBEDDED"
FAILED = "FAILED"

# Valid ``RAG_PDF_PARSER`` / ``?parser=`` values, first one being the default.
# Declared here — not in ``tasks/rag_pipeline.py`` where the parsers themselves
# live — so the API can validate the query param without importing the heavy RAG
# stack. ``rag_pipeline`` checks its own registry against this at import.
PDF_PARSERS = ("marker", "llamaparse", "docling", "pymupdf4llm")

_metadata = MetaData()

# ``markdown`` is LONGTEXT: a parsed report runs well past TEXT's 64 KB.
report_rag_table = Table(
    _TABLE,
    _metadata,
    Column("report_id", BigInteger, primary_key=True, autoincrement=False),
    Column("symbol", String(32), index=True),
    Column("title", String(512)),
    Column("pdf_url", String(1024)),
    Column("markdown", LONGTEXT),
    Column("status", String(32), index=True),
    Column("chunk_count", Integer, default=0),
    Column("collection", String(128)),
    Column("error", Text),
    Column("created_at", DateTime),
    Column("updated_at", DateTime, index=True),
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_unicode_ci",
)

# Columns a caller may write. ``report_id`` (the key) and the timestamps are
# managed here, never taken from kwargs.
_WRITABLE_COLUMNS = {
    "symbol", "title", "pdf_url", "markdown", "status", "chunk_count",
    "collection", "error",
}

_DEFAULTS: dict[str, Any] = {
    "symbol": "",
    "title": "",
    "pdf_url": "",
    "markdown": "",
    "status": PENDING,
    "chunk_count": 0,
    "collection": "",
    "error": "",
}


def endpoint() -> str:
    """The MySQL target this process reads/writes status to.

    Logged by the flow (worker) and exposed via the API so a worker-vs-API
    mismatch (each resolving different MYSQL_* env) is obvious — that mismatch is
    the usual reason a completed job's status looks "not updated".
    """
    return f"{mysql.endpoint()}.{_TABLE}"


def _engine():
    return mysql.ensure_table(_metadata, report_rag_table)


def upsert(report_id: int, **fields: Any) -> None:
    """Create the report's row, or patch the given fields if it already exists.

    Only the fields passed in are written; anything else keeps its stored value
    (or its default on first insert). ``None`` values are ignored so a caller can
    pass through optional arguments, while empty strings *are* written so fields
    like ``error`` can be cleared.
    """
    updates = {
        key: value
        for key, value in fields.items()
        if key in _WRITABLE_COLUMNS and value is not None
    }
    now = datetime.now()

    row: dict[str, Any] = {
        "report_id": int(report_id),
        **_DEFAULTS,
        **updates,
        "created_at": now,
        "updated_at": now,
    }

    stmt = mysql_insert(report_rag_table).values(**row)
    # On conflict, patch only what the caller supplied (plus updated_at) so a
    # status write never blanks out symbol/title/markdown set by an earlier step.
    stmt = stmt.on_duplicate_key_update(
        **{key: stmt.inserted[key] for key in updates},
        updated_at=stmt.inserted.updated_at,
    )

    engine = _engine()
    with engine.begin() as conn:
        conn.execute(stmt)


# ``save``/``update``/``set_status`` were three different operations on
# ClickHouse, where an INSERT and a lightweight UPDATE had to be chosen between
# carefully. On MySQL they are all the same upsert, kept as separate names for
# the existing call sites.


def save(report_id: int, **fields: Any) -> None:
    """Create or patch the report's row. Alias of :func:`upsert`."""
    upsert(report_id, **fields)


def update(report_id: int, **fields: Any) -> None:
    """Patch the report's row, creating it if absent.

    Creating-if-absent is deliberate: the old ClickHouse lightweight UPDATE was a
    silent no-op on a missing row, so a standalone
    ``python tasks/rag_pipeline.py <id>`` run (where no API call had inserted the
    row first) recorded no status at all.
    """
    upsert(report_id, **fields)


def set_status(report_id: int, status: str, **extra: Any) -> None:
    """Update a report's status (+ optional fields)."""
    upsert(report_id, status=status, **extra)


def _get_row(report_id: int) -> Optional[dict[str, Any]]:
    engine = _engine()
    with engine.connect() as conn:
        row = conn.execute(
            select(report_rag_table).where(
                report_rag_table.c.report_id == int(report_id)
            )
        ).mappings().first()
    return dict(row) if row else None


def get_status(report_id: int) -> Optional[dict[str, Any]]:
    """Return status metadata for one report (without the full markdown)."""
    row = _get_row(report_id)
    if row is None:
        return None
    return {
        "report_id": row["report_id"],
        "status": row["status"],
        "chunk_count": row["chunk_count"],
        "collection": row["collection"],
        "error": row["error"],
        "has_markdown": bool(row["markdown"]),
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else "",
    }


def get_markdown(report_id: int) -> Optional[str]:
    """Return the parsed markdown for a report, or None if not parsed yet."""
    engine = _engine()
    with engine.connect() as conn:
        markdown = conn.execute(
            select(report_rag_table.c.markdown).where(
                report_rag_table.c.report_id == int(report_id)
            )
        ).scalar_one_or_none()
    return markdown or None


def list_statuses(report_ids: Optional[list[int]] = None) -> list[dict[str, Any]]:
    """Return {report_id, status, chunk_count, updated_at} for reports.

    Without ``report_ids`` returns every tracked report — cheap, one small row
    each — so the Report page can annotate its list in a single call.
    """
    try:
        engine = _engine()
        stmt = select(
            report_rag_table.c.report_id,
            report_rag_table.c.status,
            report_rag_table.c.chunk_count,
            report_rag_table.c.updated_at,
        )
        if report_ids:
            stmt = stmt.where(
                report_rag_table.c.report_id.in_([int(r) for r in report_ids])
            )
        stmt = stmt.order_by(report_rag_table.c.updated_at.desc())

        with engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [
            {
                "report_id": r.report_id,
                "status": r.status,
                "chunk_count": r.chunk_count,
                "updated_at": r.updated_at.isoformat() if r.updated_at else "",
            }
            for r in rows
        ]
    except Exception as exc:  # noqa: BLE001 — the Report page degrades to no badges
        # loguru formats with {}, not %s — a printf placeholder here would print
        # literally and drop the exception, hiding the reason the page is empty.
        logger.warning("Failed to list RAG statuses from {}: {!r}", endpoint(), exc)
        return []
