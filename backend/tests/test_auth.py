"""Authentication: crypto primitives, the login routes, and the global guard.

The crypto tests need no database and always run. The route tests follow
``test_corporate_action_routes.py``: they bind ``get_db`` to the rolled-back
``db`` fixture and are gated on ``requires_mysql``, so the seeded user is
discarded and no real row is touched.
"""
from __future__ import annotations

from datetime import timedelta

import jwt
import pytest

from app.core import security
from app.core.security import (
    ALGORITHM,
    TokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


# --- password hashing -------------------------------------------------------

def test_hash_then_verify_accepts_the_right_password():
    digest = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", digest) is True


def test_verify_rejects_the_wrong_password():
    digest = hash_password("correct horse battery staple")
    assert verify_password("Correct horse battery staple", digest) is False


def test_two_hashes_of_one_password_differ():
    """Distinct salts. Equal digests would mean the salt is not random."""
    assert hash_password("same") != hash_password("same")


def test_verify_returns_false_for_a_malformed_hash():
    """A truncated column value must read as 'no', not raise."""
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_hash_password_rejects_an_empty_password():
    with pytest.raises(ValueError):
        hash_password("")


def test_hash_password_rejects_a_password_over_bcrypts_72_byte_limit():
    """bcrypt truncates silently at 72 bytes; surface it instead."""
    with pytest.raises(ValueError):
        hash_password("a" * 73)


# --- the signing key --------------------------------------------------------
#
# _SECRET_KEY resolves at import time, so these exercise the resolver directly.


def test_production_refuses_to_start_without_a_configured_key(monkeypatch):
    """A silent ephemeral key in production would log everyone out on restart."""
    monkeypatch.setattr(security.settings, "auth_secret_key", "")
    monkeypatch.setattr(security.settings, "environment", "production")
    with pytest.raises(RuntimeError, match="APP_AUTH_SECRET_KEY"):
        security._resolve_secret_key()


def test_development_falls_back_to_an_ephemeral_key(monkeypatch):
    monkeypatch.setattr(security.settings, "auth_secret_key", "")
    monkeypatch.setattr(security.settings, "environment", "development")
    first = security._resolve_secret_key()
    assert first
    # Random per call, which is exactly why the module resolves it only once.
    assert first != security._resolve_secret_key()


def test_a_configured_key_is_used_verbatim(monkeypatch):
    monkeypatch.setattr(security.settings, "auth_secret_key", "x" * 40)
    monkeypatch.setattr(security.settings, "environment", "production")
    assert security._resolve_secret_key() == "x" * 40

# --- tokens -----------------------------------------------------------------

def test_token_roundtrip_returns_the_subject():
    token, expires_at = create_access_token("phuc")
    assert decode_access_token(token) == "phuc"
    assert expires_at.tzinfo is not None


def test_expired_token_is_rejected():
    token, _ = create_access_token("phuc", expires_delta=timedelta(seconds=-1))
    with pytest.raises(TokenError):
        decode_access_token(token)


def test_tampered_token_is_rejected():
    token, _ = create_access_token("phuc")
    head, payload, sig = token.split(".")
    tampered = f"{head}.{payload}x.{sig}"
    with pytest.raises(TokenError):
        decode_access_token(tampered)


def test_token_signed_with_another_key_is_rejected():
    forged = jwt.encode({"sub": "phuc"}, "a-different-secret-at-least-32-bytes-long", algorithm=ALGORITHM)
    with pytest.raises(TokenError):
        decode_access_token(forged)


def test_token_without_a_subject_is_rejected():
    empty = jwt.encode({"foo": "bar"}, security._SECRET_KEY, algorithm=ALGORITHM)
    with pytest.raises(TokenError):
        decode_access_token(empty)


def test_garbage_input_raises_rather_than_returning_something_truthy():
    with pytest.raises(TokenError):
        decode_access_token("not.a.token")


# --- login routes -----------------------------------------------------------
#
# Everything below runs against MySQL through the rolled-back ``db`` fixture,
# following the pattern in test_corporate_action_routes.py.

from fastapi.testclient import TestClient  # noqa: E402

from app.db.base import get_db  # noqa: E402
from app.db.models.user import User  # noqa: E402
from app.main import app  # noqa: E402
from tests.conftest import requires_mysql  # noqa: E402

PASSWORD = "s3cret-test-password"


@pytest.fixture
def seeded_user(db):
    """An active user that disappears with the fixture's rollback."""
    user = User(
        username="route-test-user",
        password_hash=hash_password(PASSWORD),
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)


@requires_mysql
def test_login_returns_a_usable_token(client, seeded_user):
    res = client.post(
        "/api/v1/auth/login",
        json={"username": seeded_user.username, "password": PASSWORD},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["token_type"] == "bearer"
    assert decode_access_token(body["access_token"]) == seeded_user.username
    assert body["expires_at"]


@requires_mysql
def test_login_rejects_the_wrong_password(client, seeded_user):
    res = client.post(
        "/api/v1/auth/login",
        json={"username": seeded_user.username, "password": "wrong"},
    )
    assert res.status_code == 401


@requires_mysql
def test_login_rejects_an_unknown_username(client):
    res = client.post(
        "/api/v1/auth/login",
        json={"username": "nobody-by-that-name", "password": PASSWORD},
    )
    assert res.status_code == 401


@requires_mysql
def test_login_rejects_a_deactivated_user(client, db, seeded_user):
    seeded_user.is_active = False
    db.flush()
    res = client.post(
        "/api/v1/auth/login",
        json={"username": seeded_user.username, "password": PASSWORD},
    )
    assert res.status_code == 401


@requires_mysql
def test_login_does_not_reveal_whether_the_username_exists(client, seeded_user):
    """Identical wording for a bad password and an unknown user."""
    unknown = client.post(
        "/api/v1/auth/login",
        json={"username": "nobody-by-that-name", "password": PASSWORD},
    )
    bad_password = client.post(
        "/api/v1/auth/login",
        json={"username": seeded_user.username, "password": "wrong"},
    )
    assert unknown.json()["detail"] == bad_password.json()["detail"]


# --- the global guard -------------------------------------------------------
#
# These opt out of the conftest autouse override with @pytest.mark.real_auth so
# they exercise the real dependency rather than the stubbed one.


@pytest.mark.real_auth
@requires_mysql
def test_protected_route_401s_without_a_token(client):
    assert client.get("/api/v1/portfolio/positions").status_code == 401


@pytest.mark.real_auth
@requires_mysql
def test_protected_route_401s_on_a_malformed_header(client):
    res = client.get(
        "/api/v1/portfolio/positions", headers={"Authorization": "Basic zzzz"}
    )
    assert res.status_code == 401


@pytest.mark.real_auth
@requires_mysql
def test_protected_route_401s_on_a_forged_token(client):
    forged = jwt.encode(
        {"sub": "phuc"}, "a-different-secret-at-least-32-bytes-long", algorithm=ALGORITHM
    )
    res = client.get(
        "/api/v1/portfolio/positions", headers={"Authorization": f"Bearer {forged}"}
    )
    assert res.status_code == 401


@pytest.mark.real_auth
@requires_mysql
def test_protected_route_accepts_a_valid_token(client, seeded_user):
    token, _ = create_access_token(seeded_user.username)
    res = client.get(
        "/api/v1/portfolio/positions", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code != 401


@pytest.mark.real_auth
@requires_mysql
def test_token_for_a_deactivated_user_is_rejected(client, db, seeded_user):
    """Revocation must bite immediately, not at token expiry."""
    token, _ = create_access_token(seeded_user.username)
    seeded_user.is_active = False
    db.flush()
    res = client.get(
        "/api/v1/portfolio/positions", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 401


@pytest.mark.real_auth
@requires_mysql
def test_token_for_a_deleted_user_is_rejected(client, db, seeded_user):
    token, _ = create_access_token("someone-who-was-never-seeded")
    res = client.get(
        "/api/v1/portfolio/positions", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 401


@pytest.mark.real_auth
@requires_mysql
def test_me_returns_the_callers_username(client, seeded_user):
    token, _ = create_access_token(seeded_user.username)
    res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    assert res.json()["username"] == seeded_user.username


@pytest.mark.real_auth
@requires_mysql
def test_health_is_reachable_without_a_token(client):
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


@pytest.mark.real_auth
@requires_mysql
def test_docs_and_openapi_are_reachable_without_a_token(client):
    """FastAPI adds these as Starlette routes, so app-level dependencies do not
    apply. Asserted rather than assumed — it is the carve-out the design relies
    on, and a FastAPI upgrade could change it."""
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
