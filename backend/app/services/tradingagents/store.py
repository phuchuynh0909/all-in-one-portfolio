"""Persistence for completed TradingAgents analyses (MySQL).

Each finished run — every agent report plus the final decision and run
metadata — is saved as one row so the frontend can list past analyses and
reopen any of them. The full per-agent reports are stored as a JSON blob in
``sections``; list queries return only metadata + a short snippet to stay cheap.

Moved from ClickHouse to MySQL so agent history lives with the rest of the
app's primary store (portfolio, reports, RAG state) and is reachable from every
process that needs it. The engine and schema bootstrap are shared via
``app.db.mysql``, so the table is created on first use and there is no
migration step to run.

The public functions and their return shapes are unchanged, so the route layer,
``runner`` and ``past_runs`` are untouched.

Config: ``MYSQL_HOST/PORT/USER/PASSWORD/DB``, or ``MYSQL_URL`` to override the
whole DSN. See ``app/core/settings.py``.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any, Optional

from loguru import logger
from sqlalchemy import (
    Column,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    and_,
    func,
    insert,
    select,
    text,
)
from sqlalchemy.dialects.mysql import DATETIME, LONGTEXT

from app.db import mysql

_TABLE = os.getenv("MYSQL_TRADING_AGENTS_TABLE", "trading_agent_analyses")

_metadata = MetaData()

# NOTE: `signal` is a reserved word in MySQL. Every query here goes through
# SQLAlchemy Core, which quotes identifiers, so the column keeps its name and
# the API shape is preserved. Do not hand-write SQL against this table.
_analyses = Table(
    _TABLE,
    _metadata,
    Column("id", String(32), primary_key=True),
    Column("symbol", String(32), nullable=False),
    Column("trade_date", String(10), nullable=False),
    Column("provider", String(128), nullable=False, server_default=""),
    Column("model", String(255), nullable=False, server_default=""),
    Column("signal", String(64), nullable=False, server_default=""),
    # JSON blobs, kept as text so the round-trip matches what ClickHouse held.
    Column("analysts", LONGTEXT),
    Column("sections", LONGTEXT),
    Column("final_decision", LONGTEXT),
    Column("duration_ms", Integer, nullable=False, server_default="0"),
    # Millisecond precision, matching the previous DateTime64(3): two runs of
    # the same ticker+date must be distinguishable by recency.
    Column(
        "created_at",
        DATETIME(fsp=3),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(3)"),
    ),
    # Serves both the per-symbol history list and the prior-decision lookup.
    Index("ix_taa_symbol_trade_date", "symbol", "trade_date"),
    Index("ix_taa_created_at", "created_at"),
    mysql_charset="utf8mb4",
    mysql_collate="utf8mb4_unicode_ci",
)


def _engine():
    return mysql.ensure_table(_metadata, _analyses)


def _iso(value: Any) -> str:
    """Datetimes come back as objects; empty string when the row has none."""
    return value.isoformat() if value is not None else ""


def save_analysis(
    *,
    symbol: str,
    trade_date: str,
    provider: str,
    model: str,
    signal: str,
    analysts: list[str],
    sections: dict[str, str],
    final_decision: str,
    duration_ms: int = 0,
) -> str:
    """Insert one completed analysis; returns its generated id."""
    analysis_id = uuid.uuid4().hex
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(
            insert(_analyses).values(
                id=analysis_id,
                symbol=symbol.upper(),
                trade_date=trade_date,
                provider=provider,
                model=model,
                signal=signal,
                analysts=json.dumps(analysts, ensure_ascii=False),
                sections=json.dumps(sections, ensure_ascii=False),
                final_decision=final_decision,
                duration_ms=int(duration_ms),
            )
        )
    return analysis_id


def list_analyses(symbol: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
    """List saved analyses (metadata + snippet), newest first."""
    try:
        engine = _engine()
        stmt = (
            select(
                _analyses.c.id,
                _analyses.c.symbol,
                _analyses.c.trade_date,
                _analyses.c.provider,
                _analyses.c.model,
                _analyses.c.signal,
                func.substring(_analyses.c.final_decision, 1, 240).label("snippet"),
                _analyses.c.duration_ms,
                _analyses.c.created_at,
            )
            .order_by(_analyses.c.created_at.desc())
            .limit(limit)
        )
        if symbol:
            stmt = stmt.where(_analyses.c.symbol == symbol.upper())

        with engine.connect() as conn:
            rows = conn.execute(stmt).all()

        return [
            {
                "id": r.id,
                "symbol": r.symbol,
                "trade_date": r.trade_date,
                "provider": r.provider,
                "model": r.model,
                "signal": r.signal,
                "snippet": r.snippet or "",
                "duration_ms": r.duration_ms,
                "created_at": _iso(r.created_at),
            }
            for r in rows
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to list analyses: {!r}", exc)
        return []


def list_prior_decisions(
    symbol: str,
    before_trade_date: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Past decisions for one symbol, strictly before a trade date, newest first.

    Feeds ``past_runs.build_past_context``. Two deliberate choices:

    * The cutoff is on ``trade_date``, not ``created_at`` — a run replayed over
      history must not read analyses of *later* sessions, however recently they
      were computed.
    * Only the newest run per session is kept, so re-running the same
      ticker+date does not spend the budget on duplicates. ClickHouse expressed
      this as ``LIMIT 1 BY trade_date``; MySQL has no equivalent, so it is a
      join against the latest ``created_at`` per ``trade_date``. That is written
      without window functions so it holds on MySQL 5.7 as well as 8+, and the
      result is de-duplicated again in Python to stay exact if two runs ever
      land on the same millisecond.

    Returns [] on any failure — prior context is an enrichment, never a
    precondition for the next run.
    """
    try:
        engine = _engine()
        sym = symbol.upper()
        scope = and_(
            _analyses.c.symbol == sym,
            _analyses.c.trade_date < before_trade_date,
        )

        # Latest run per session, within the cutoff.
        latest = (
            select(
                _analyses.c.trade_date.label("trade_date"),
                func.max(_analyses.c.created_at).label("mx"),
            )
            .where(scope)
            .group_by(_analyses.c.trade_date)
            .subquery()
        )

        stmt = (
            select(
                _analyses.c.trade_date,
                _analyses.c.signal,
                func.substring(_analyses.c.final_decision, 1, 4000).label("final_decision"),
                _analyses.c.created_at,
            )
            .join(
                latest,
                and_(
                    _analyses.c.trade_date == latest.c.trade_date,
                    _analyses.c.created_at == latest.c.mx,
                ),
            )
            .where(scope)
            .order_by(_analyses.c.trade_date.desc(), _analyses.c.created_at.desc())
            # Room for same-millisecond ties before the Python de-dup trims back.
            .limit(limit * 2)
        )

        with engine.connect() as conn:
            rows = conn.execute(stmt).all()

        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for r in rows:
            if r.trade_date in seen:
                continue
            seen.add(r.trade_date)
            out.append(
                {
                    "trade_date": r.trade_date,
                    "signal": r.signal,
                    "final_decision": r.final_decision or "",
                    "created_at": _iso(r.created_at),
                }
            )
            if len(out) >= limit:
                break
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to list prior decisions for {}: {!r}", symbol, exc)
        return []


def get_analysis(analysis_id: str) -> Optional[dict[str, Any]]:
    """Fetch one saved analysis with its full per-agent sections."""
    try:
        engine = _engine()
        stmt = select(
            _analyses.c.id,
            _analyses.c.symbol,
            _analyses.c.trade_date,
            _analyses.c.provider,
            _analyses.c.model,
            _analyses.c.signal,
            _analyses.c.analysts,
            _analyses.c.sections,
            _analyses.c.final_decision,
            _analyses.c.duration_ms,
            _analyses.c.created_at,
        ).where(_analyses.c.id == analysis_id).limit(1)

        with engine.connect() as conn:
            r = conn.execute(stmt).first()
        if r is None:
            return None

        return {
            "id": r.id,
            "symbol": r.symbol,
            "trade_date": r.trade_date,
            "provider": r.provider,
            "model": r.model,
            "signal": r.signal,
            "analysts": json.loads(r.analysts) if r.analysts else [],
            "sections": json.loads(r.sections) if r.sections else {},
            "final_decision": r.final_decision or "",
            "duration_ms": r.duration_ms,
            "created_at": _iso(r.created_at),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to get analysis {}: {!r}", analysis_id, exc)
        return None
