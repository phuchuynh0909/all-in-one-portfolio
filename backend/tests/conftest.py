"""Shared fixtures for tests that hit the real MySQL store."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.core.settings import settings
from app.db.base import SessionLocal, engine

# This probe runs at import time, so collection blocks on it. Without a timeout
# an unreachable host hangs until the OS gives up on the TCP connect — two
# transient blips have already been observed. Probed on a throwaway engine so
# the application's own pool settings are left alone.
_PROBE_CONNECT_TIMEOUT = 3


def mysql_available() -> bool:
    if not settings.database_url.startswith("mysql"):
        return False
    probe = create_engine(
        settings.database_url,
        poolclass=NullPool,
        connect_args={"connect_timeout": _PROBE_CONNECT_TIMEOUT},
    )
    try:
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except (SQLAlchemyError, OSError):
        # Any failure to reach the server means "skip", not "crash collection".
        return False
    finally:
        probe.dispose()


requires_mysql = pytest.mark.skipif(
    not mysql_available(),
    reason=f"MySQL not reachable at {settings.mysql_host}:{settings.mysql_port}",
)


@pytest.fixture
def db():
    """A session whose work is always rolled back.

    The service functions call ``commit()``, which would normally end the outer
    transaction. Binding the session to a connection that already has one open
    nests them, so the outer ``rollback()`` still discards everything — which is
    what keeps these tests off the real 6,349 rows.
    """
    connection = engine.connect()
    outer = connection.begin()
    session = SessionLocal(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()
