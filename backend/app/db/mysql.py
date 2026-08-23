"""Shared MySQL engine + schema bootstrap for the report stores.

Two modules keep report state in MySQL — ``app.stores.raw_wichart_report``
(report details / ``llm_summary``) and ``app.services.report_rag_service`` (the
RAG pipeline's status + parsed markdown). Both need the same three things: one
engine per process, the database created if it doesn't exist, and their table
created on first use. That lives here once.

Tables are created on demand rather than by a migration, matching how these
stores already behaved on ClickHouse/Delta — there is no migration step to run.

Config: ``MYSQL_HOST/PORT/USER/PASSWORD/DB``, or ``MYSQL_URL`` for the whole DSN.
See ``app/core/settings.py``.
"""
from __future__ import annotations

from typing import Optional

from loguru import logger
from sqlalchemy import MetaData, Table, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import URL, make_url

from app.core.settings import settings

_engine: Optional[Engine] = None
_database_ready = False
_tables_ready: set[str] = set()


def endpoint() -> str:
    """``host:port/db`` this process reads/writes, for logs and health output."""
    return f"{settings.mysql_host}:{settings.mysql_port}/{settings.mysql_db}"


def get_engine() -> Engine:
    """Process-wide engine.

    ``pool_pre_ping`` so a connection dropped by the server (or by a long idle
    Prefect worker) is replaced instead of raising, and ``pool_recycle`` keeps
    connections under MySQL's default ``wait_timeout``.
    """
    global _engine
    if _engine is None:
        logger.debug("MySQL endpoint: {}", endpoint())
        _engine = create_engine(
            settings.mysql_url,
            pool_pre_ping=True,
            pool_recycle=3600,
            future=True,
        )
    return _engine


def ensure_database() -> None:
    """``CREATE DATABASE IF NOT EXISTS`` — once per process."""
    global _database_ready
    if _database_ready:
        return

    url = make_url(settings.mysql_url)
    # A server-level URL: rebuilt rather than ``url.set(database=None)``, which
    # silently ignores None and would keep targeting the missing database.
    server = create_engine(
        URL.create(
            drivername=url.drivername,
            username=url.username,
            password=url.password,
            host=url.host,
            port=url.port,
            query=url.query,
        ),
        future=True,
    )
    try:
        with server.connect() as conn:
            conn.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{url.database}` "
                    f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
            conn.commit()
    finally:
        server.dispose()

    _database_ready = True


def ensure_table(metadata: MetaData, table: Table) -> Engine:
    """Ensure the database and one table exist; returns the engine.

    Cheap to call on every operation — the work is done once per process per
    table, then short-circuits.
    """
    engine = get_engine()
    if table.name in _tables_ready:
        return engine
    ensure_database()
    metadata.create_all(engine, tables=[table])
    _tables_ready.add(table.name)
    return engine
