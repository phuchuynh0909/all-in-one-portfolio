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


@pytest.fixture(autouse=True)
def _authenticated(request):
    """Run every test as a logged-in user.

    The app guards all routes by default, which would 401 the six modules that
    build a ``TestClient``. Overriding the dependency here fixes all of them
    without touching a single one. A test that wants the real guard opts out
    with ``@pytest.mark.real_auth``.
    """
    if "real_auth" in request.keywords:
        yield
        return

    from app.api.deps import require_user
    from app.db.models.user import User
    from app.main import app

    app.dependency_overrides[require_user] = lambda: User(
        id=1, username="test-user", is_active=True
    )
    try:
        yield
    finally:
        app.dependency_overrides.pop(require_user, None)
