"""Persistence for completed TradingAgents analyses (ClickHouse).

Each finished run — every agent report plus the final decision and run
metadata — is saved as one row so the frontend can list past analyses and
reopen any of them. The full per-agent reports are stored as a JSON blob in
``sections``; list queries return only metadata + a short snippet to stay cheap.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any, Optional

import clickhouse_connect
from loguru import logger

from app.core.settings import settings

_TABLE = os.getenv("CLICKHOUSE_TRADING_AGENTS_TABLE", "trading_agent_analyses")


def _client():
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_db,
    )


def _ensure_table(client) -> None:
    db = settings.clickhouse_db
    client.command(f"CREATE DATABASE IF NOT EXISTS {db}")
    client.command(
        f"""
        CREATE TABLE IF NOT EXISTS {db}.{_TABLE} (
            id String,
            symbol String,
            trade_date String,
            provider String,
            model String,
            signal String,
            analysts String,           -- JSON array
            sections String,           -- JSON object {{section: markdown}}
            final_decision String,
            duration_ms UInt32,
            created_at DateTime64(3) DEFAULT now64(3)
        )
        ENGINE = MergeTree
        ORDER BY (created_at, id)
        """
    )


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
    client = _client()
    try:
        _ensure_table(client)
        client.insert(
            table=f"{settings.clickhouse_db}.{_TABLE}",
            data=[[
                analysis_id,
                symbol.upper(),
                trade_date,
                provider,
                model,
                signal,
                json.dumps(analysts, ensure_ascii=False),
                json.dumps(sections, ensure_ascii=False),
                final_decision,
                int(duration_ms),
            ]],
            column_names=[
                "id", "symbol", "trade_date", "provider", "model", "signal",
                "analysts", "sections", "final_decision", "duration_ms",
            ],
        )
        return analysis_id
    finally:
        client.close()


def list_analyses(symbol: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
    """List saved analyses (metadata + snippet), newest first."""
    client = _client()
    try:
        _ensure_table(client)
        query = (
            "SELECT id, symbol, trade_date, provider, model, signal, "
            "substring(final_decision, 1, 240) AS snippet, duration_ms, created_at "
            f"FROM {settings.clickhouse_db}.{_TABLE} "
        )
        params: dict[str, Any] = {"limit": limit}
        if symbol:
            query += "WHERE symbol = %(symbol)s "
            params["symbol"] = symbol.upper()
        query += "ORDER BY created_at DESC LIMIT %(limit)s"

        result = client.query(query, parameters=params)
        rows = []
        for r in result.result_rows:
            rows.append({
                "id": r[0],
                "symbol": r[1],
                "trade_date": r[2],
                "provider": r[3],
                "model": r[4],
                "signal": r[5],
                "snippet": r[6],
                "duration_ms": r[7],
                "created_at": r[8].isoformat() if r[8] is not None else "",
            })
        return rows
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to list analyses: {!r}", exc)
        return []
    finally:
        client.close()


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
    * ``LIMIT 1 BY trade_date`` keeps only the newest run per session, so
      re-running the same ticker+date does not spend the budget on duplicates.

    Returns [] on any failure — prior context is an enrichment, never a
    precondition for the next run.
    """
    client = _client()
    try:
        _ensure_table(client)
        query = (
            "SELECT trade_date, signal, substring(final_decision, 1, 4000), created_at "
            f"FROM {settings.clickhouse_db}.{_TABLE} "
            "WHERE symbol = %(symbol)s AND trade_date < %(before)s "
            "ORDER BY trade_date DESC, created_at DESC "
            "LIMIT 1 BY trade_date "
            "LIMIT %(limit)s"
        )
        result = client.query(
            query,
            parameters={
                "symbol": symbol.upper(),
                "before": before_trade_date,
                "limit": limit,
            },
        )
        return [
            {
                "trade_date": r[0],
                "signal": r[1],
                "final_decision": r[2],
                "created_at": r[3].isoformat() if r[3] is not None else "",
            }
            for r in result.result_rows
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to list prior decisions for {}: {!r}", symbol, exc)
        return []
    finally:
        client.close()


def get_analysis(analysis_id: str) -> Optional[dict[str, Any]]:
    """Fetch one saved analysis with its full per-agent sections."""
    client = _client()
    try:
        _ensure_table(client)
        query = (
            "SELECT id, symbol, trade_date, provider, model, signal, analysts, "
            "sections, final_decision, duration_ms, created_at "
            f"FROM {settings.clickhouse_db}.{_TABLE} "
            "WHERE id = %(id)s LIMIT 1"
        )
        result = client.query(query, parameters={"id": analysis_id})
        if not result.result_rows:
            return None
        r = result.result_rows[0]
        return {
            "id": r[0],
            "symbol": r[1],
            "trade_date": r[2],
            "provider": r[3],
            "model": r[4],
            "signal": r[5],
            "analysts": json.loads(r[6]) if r[6] else [],
            "sections": json.loads(r[7]) if r[7] else {},
            "final_decision": r[8],
            "duration_ms": r[9],
            "created_at": r[10].isoformat() if r[10] is not None else "",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to get analysis {}: {!r}", analysis_id, exc)
        return None
    finally:
        client.close()
