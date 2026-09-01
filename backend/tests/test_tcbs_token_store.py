"""The TCBS credential row: round-trip, upsert, expiry."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import tcbs_token_store as store
from app.services.tcbs_token_store import TcbsCredentials
from tests.conftest import requires_mysql


def _creds(**over) -> TcbsCredentials:
    base = dict(
        client_id="client-abc",
        client_secret="secret-xyz",
        access_token="access-1",
        refresh_token="refresh-1",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    base.update(over)
    return TcbsCredentials(**base)


def test_is_expired_false_when_comfortably_ahead():
    assert _creds().is_expired() is False


def test_is_expired_true_inside_the_skew_window():
    creds = _creds(expires_at=datetime.now(timezone.utc) + timedelta(seconds=30))
    assert creds.is_expired(skew_seconds=60) is True


def test_is_expired_false_when_no_expiry_is_known():
    # A token with no stated expiry is used until the server rejects it; the
    # 401-refresh path is what recovers, not a guess here.
    assert _creds(expires_at=None).is_expired() is False


@requires_mysql
def test_save_then_load_round_trips(db, monkeypatch):
    monkeypatch.setattr(store, "SessionLocal", lambda: db)
    store.save(_creds())
    loaded = store.load()
    assert loaded is not None
    assert loaded.client_id == "client-abc"
    assert loaded.access_token == "access-1"
    assert loaded.refresh_token == "refresh-1"


@requires_mysql
def test_save_upserts_rather_than_appending(db, monkeypatch):
    monkeypatch.setattr(store, "SessionLocal", lambda: db)
    store.save(_creds())
    store.save(_creds(access_token="access-2", refresh_token="refresh-2"))
    loaded = store.load()
    assert loaded.access_token == "access-2"
    assert db.query(store.TcbsOAuthToken).count() == 1


@requires_mysql
def test_load_returns_none_when_never_logged_in(db, monkeypatch):
    monkeypatch.setattr(store, "SessionLocal", lambda: db)
    assert store.load() is None


@requires_mysql
def test_clear_reports_whether_a_row_went(db, monkeypatch):
    monkeypatch.setattr(store, "SessionLocal", lambda: db)
    assert store.clear() is False
    store.save(_creds())
    assert store.clear() is True
    assert store.load() is None
