"""Shared fixtures for tests that hit the real MySQL store."""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.settings import settings
from app.db.base import SessionLocal, engine


def mysql_available() -> bool:
    if not settings.database_url.startswith("mysql"):
        return False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


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
