"""ClickHouse-backed status + parsed-markdown store for the report RAG pipeline.

One row per report tracks the RAG lifecycle and holds the markdown produced by
parsing the PDF. The Prefect flow (``tasks/rag_pipeline.py``) drives the status
transitions; the API reads them so the Report page can show which reports are
already embedded.

Status lifecycle:
    PENDING -> PARSING -> PARSED -> EMBEDDING -> EMBEDDED
    (any step may transition to FAILED; ``error`` holds the reason)

Table: ``report_rag`` (ReplacingMergeTree(updated_at), ORDER BY report_id).
Reads use FINAL so only the latest version per report is returned.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

import clickhouse_connect
from loguru import logger

from app.core.settings import settings

_TABLE = os.getenv("CLICKHOUSE_REPORT_RAG_TABLE", "report_rag")

# Status constants
PENDING = "PENDING"
PARSING = "PARSING"
PARSED = "PARSED"
EMBEDDING = "EMBEDDING"
EMBEDDED = "EMBEDDED"
FAILED = "FAILED"

_COLUMNS = [
    "report_id", "symbol", "title", "pdf_url", "markdown",
    "status", "chunk_count", "collection", "error",
    "created_at", "updated_at",
]


def endpoint() -> str:
    """The ClickHouse target this process reads/writes status to.

    Logged by the flow (worker) and exposed via the API so a worker-vs-API
    endpoint mismatch (e.g. host-run worker vs containerized API resolving
    different CLICKHOUSE_* env) is obvious — that mismatch is the usual reason a
    completed job's status looks "not updated".
    """
    return (
        f"{settings.clickhouse_host}:{settings.clickhouse_port}/"
        f"{settings.clickhouse_db}.{_TABLE}"
    )


def _client():
    logger.debug("report_rag ClickHouse endpoint: {}", endpoint())
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_db,
    )


# One-time-per-process guard for the (idempotent but non-free) migration that
# enables lightweight updates on a pre-existing table.
_lightweight_ready = False


def _ensure_table(client) -> None:
    global _lightweight_ready
    db = settings.clickhouse_db
    client.command(f"CREATE DATABASE IF NOT EXISTS {db}")
    # New tables are created ready for lightweight UPDATE: CH 25.7+ requires a
    # materialized _block_number column (enable_block_number_column = 1).
    client.command(
        f"""
        CREATE TABLE IF NOT EXISTS {db}.{_TABLE} (
            report_id Int64,
            symbol String,
            title String,
            pdf_url String,
            markdown String,
            status String,
            chunk_count UInt32,
            collection String,
            error String,
            created_at DateTime64(3),
            updated_at DateTime64(3)
        )
        ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY report_id
        SETTINGS enable_block_number_column = 1, enable_block_offset_column = 1
        """
    )

    # A table created before these settings existed needs them turned on (and its
    # existing parts rewritten so _block_number/_block_offset are materialized).
    # Best-effort, once per process — harmless if already enabled or unsupported.
    if not _lightweight_ready:
        for cmd in (
            f"ALTER TABLE {db}.{_TABLE} MODIFY SETTING "
            "enable_block_number_column = 1, enable_block_offset_column = 1",
            f"OPTIMIZE TABLE {db}.{_TABLE} FINAL",
        ):
            try:
                client.command(cmd)
            except Exception as exc:  # noqa: BLE001
                logger.debug("report_rag migration step skipped ({}): {}", cmd, exc)
        _lightweight_ready = True


def _get_row(client, report_id: int) -> Optional[dict[str, Any]]:
    query = (
        f"SELECT {', '.join(_COLUMNS)} FROM {settings.clickhouse_db}.{_TABLE} FINAL "
        "WHERE report_id = %(rid)s LIMIT 1"
    )
    result = client.query(query, parameters={"rid": int(report_id)})
    if not result.result_rows:
        return None
    return dict(zip(_COLUMNS, result.result_rows[0]))


def upsert(report_id: int, **fields: Any) -> None:
    """Insert a new ReplacingMergeTree version for the report.

    Prefer :func:`save` for normal pipeline writes — mixing ``INSERT`` (upsert)
    with later lightweight ``UPDATE``s can leave FINAL showing an older empty
    PENDING row (symbol/title/pdf_url blank) while status advances on a patch.
    """
    client = _client()
    try:
        _ensure_table(client)
        existing = _get_row(client, report_id) or {}
        now = datetime.utcnow()

        row = {
            "report_id": int(report_id),
            "symbol": existing.get("symbol", ""),
            "title": existing.get("title", ""),
            "pdf_url": existing.get("pdf_url", ""),
            "markdown": existing.get("markdown", ""),
            "status": existing.get("status", PENDING),
            "chunk_count": existing.get("chunk_count", 0),
            "collection": existing.get("collection", ""),
            "error": existing.get("error", ""),
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
        }
        # Apply provided overrides (ignore unknown keys defensively).
        # Allow empty strings (e.g. error="") so fields can be cleared.
        for key, value in fields.items():
            if key in row and value is not None:
                row[key] = value

        client.insert(
            table=f"{settings.clickhouse_db}.{_TABLE}",
            data=[[row[c] for c in _COLUMNS]],
            column_names=_COLUMNS,
        )
    finally:
        client.close()


def save(report_id: int, **fields: Any) -> None:
    """Write fields without fighting lightweight updates.

    - Row missing → ``upsert`` (first INSERT, creates the row).
    - Row exists → ``update`` (in-place patch). Avoids a second INSERT that
      ReplacingMergeTree + later UPDATEs can "lose" for non-key columns when
      FINAL resolves versions.
    """
    client = _client()
    try:
        _ensure_table(client)
        exists = _get_row(client, report_id) is not None
    finally:
        client.close()

    if exists:
        update(report_id, **fields)
    else:
        upsert(report_id, **fields)


# Columns update/set_status may modify in place — never the ORDER BY key
# (report_id) or created_at / updated_at (version key).
_UPDATABLE_COLUMNS = {
    "symbol", "title", "pdf_url", "markdown", "status", "chunk_count",
    "collection", "error",
}

# clickhouse_connect inlines %(params)s into the SQL text. Default
# max_query_size is 256 KiB — a full report markdown easily exceeds that and
# fails with "Max query size exceeded". Large payloads go through INSERT upsert
# (data in the HTTP body) instead of UPDATE.
_MAX_UPDATE_INLINE_CHARS = 80_000


def update(report_id: int, **fields: Any) -> None:
    """Patch an existing report row in place (ClickHouse lightweight UPDATE).

    Uses ``UPDATE ... SET ... WHERE`` so changes are immediately visible —
    no ReplacingMergeTree version to merge. The row must already exist (created
    by ``upsert``); a lightweight UPDATE on a missing row is a no-op.

    Large string fields (esp. ``markdown``) fall back to :func:`upsert` because
    parameterized UPDATEs embed values in the query text and hit
    ``max_query_size``.

    NB: ``updated_at`` is the ReplacingMergeTree version key and cannot be
    changed by a lightweight UPDATE ("Cannot UPDATE key column"), so it is left
    at its last-upsert value; only non-key columns are patched here.
    """
    updates: dict[str, Any] = {}
    for key, value in fields.items():
        if key in _UPDATABLE_COLUMNS and value is not None:
            updates[key] = value
    if not updates:
        return

    if any(
        isinstance(v, str) and len(v) > _MAX_UPDATE_INLINE_CHARS
        for v in updates.values()
    ):
        upsert(report_id, **updates)
        return

    set_clause = ", ".join(f"{col} = %({col})s" for col in updates)
    params: dict[str, Any] = dict(updates)
    params["rid"] = int(report_id)
    sql = (
        f"UPDATE {settings.clickhouse_db}.{_TABLE} SET {set_clause} "
        "WHERE report_id = %(rid)s"
    )

    client = _client()
    try:
        _ensure_table(client)
        try:
            # Lightweight updates need this setting on 25.7; on versions where
            # they are GA (and the setting was removed) retry without it.
            client.command(
                sql, parameters=params,
                settings={"allow_experimental_lightweight_update": 1},
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "unknown setting" in msg:
                client.command(sql, parameters=params)
            elif "max query size" in msg:
                # Still too large for UPDATE — rewrite via INSERT body.
                upsert(report_id, **updates)
            else:
                raise
    finally:
        client.close()


def set_status(report_id: int, status: str, **extra: Any) -> None:
    """Update a report's status (+ optional fields) in place."""
    update(report_id, status=status, **extra)


def get_status(report_id: int) -> Optional[dict[str, Any]]:
    """Return status metadata for one report (without the full markdown)."""
    client = _client()
    try:
        _ensure_table(client)
        row = _get_row(client, report_id)
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
    finally:
        client.close()


def get_markdown(report_id: int) -> Optional[str]:
    """Return the parsed markdown for a report, or None if not parsed yet."""
    client = _client()
    try:
        _ensure_table(client)
        row = _get_row(client, report_id)
        return row["markdown"] if row and row["markdown"] else None
    finally:
        client.close()


def list_statuses(report_ids: Optional[list[int]] = None) -> list[dict[str, Any]]:
    """Return {report_id, status, chunk_count, updated_at} for reports.

    Without ``report_ids`` returns every tracked report — cheap, one small row
    each — so the Report page can annotate its list in a single call.
    """
    client = _client()
    try:
        _ensure_table(client)
        query = (
            f"SELECT report_id, status, chunk_count, updated_at "
            f"FROM {settings.clickhouse_db}.{_TABLE} FINAL "
        )
        params: dict[str, Any] = {}
        if report_ids:
            query += "WHERE report_id IN %(ids)s "
            params["ids"] = [int(r) for r in report_ids]
        query += "ORDER BY updated_at DESC"

        result = client.query(query, parameters=params or None)
        return [
            {
                "report_id": r[0],
                "status": r[1],
                "chunk_count": r[2],
                "updated_at": r[3].isoformat() if r[3] else "",
            }
            for r in result.result_rows
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to list RAG statuses: %s", exc)
        return []
    finally:
        client.close()
