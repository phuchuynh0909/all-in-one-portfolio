# Simple Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put every backend route and every frontend page behind a username/password login for a small set of seeded users.

**Architecture:** A `users` table holds bcrypt password hashes. `POST /auth/login` returns a 30-day HS256 JWT. A `require_user` dependency registered app-wide on the FastAPI instance rejects any request without a valid token, exempting only `/api/v1/health` and `/api/v1/auth/login`. The frontend stores the token in `localStorage` and a scoped `window.fetch` interceptor attaches `Authorization: Bearer` to API-bound requests only — this is required because ~76 raw `fetch()` calls bypass `apiGet`/`apiPost`.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (sync), Pydantic v2, alembic, `PyJWT`, `bcrypt`, loguru · React 18, TypeScript, MUI 5, react-router-dom 6, TanStack Query

**Spec:** `docs/superpowers/specs/2026-09-01-simple-auth-design.md`

## Global Constraints

- **Backend verify command:** `cd backend && pytest tests`. NEVER bare `pytest` from the repo root — it collects `testing/test_dnse_api.py`, which fires a live signed DNSE trading request at import time.
- **Frontend verify commands:** `cd frontend && npm run build` (`tsc && vite build`) and `cd frontend && npm run lint` (`eslint --max-warnings 0` — zero warnings tolerated).
- **Alembic head is `b7e2f4c81d35`** (`seed_sector_level_5`). The spec's guess of `d5a91c3e7b20` was wrong; the new migration's `down_revision` is `b7e2f4c81d35`.
- **The Docker image does not copy `backend/scripts/`.** It copies only `app`, `tasks`, `alembic`, `alembic.ini`, `libs`. Anything that must run in the container lives under `backend/app/`.
- **The image installs `requirements.lock.txt`, not `requirements.txt`.** After editing the latter, run `make lock-backend` or the build silently keeps the old resolution.
- **Never print, commit, or echo the contents of `.env` or `prod.env`.** Append to `.env` with `>>`; do not read it back.
- **Production is off-limits.** No `make prod-*` command is part of this work.
- Python: loguru (`from loguru import logger`), Pydantic v2, SQLAlchemy 2.0 style. TypeScript: strict, MUI for UI.
- Keep changes narrow. Do not refactor unrelated code.

---

### Task 1: Password hashing and JWT primitives

Pure functions with no FastAPI and no database, so they are fully testable on their own.

**Files:**
- Create: `backend/app/core/security.py`
- Create: `backend/tests/test_auth.py`
- Modify: `backend/requirements.txt`
- Modify: `backend/app/core/settings.py`

**Interfaces:**
- Consumes: `settings.environment` (already exists).
- Produces:
  - `hash_password(password: str) -> str`
  - `verify_password(password: str, password_hash: str) -> bool`
  - `create_access_token(subject: str, expires_delta: timedelta | None = None) -> tuple[str, datetime]`
  - `decode_access_token(token: str) -> str` (returns the subject; raises `TokenError`)
  - `class TokenError(Exception)`
  - `ALGORITHM = "HS256"`, module global `_SECRET_KEY`
  - `settings.auth_secret_key: str`, `settings.auth_token_ttl_days: int`

- [ ] **Step 1: Install the two new dependencies locally and declare them**

The local environment runs pytest, so it needs the packages too — not just the image.

```bash
cd backend && python -m pip install "PyJWT>=2.8.0" "bcrypt>=4.1.0"
```

Add to `backend/requirements.txt` immediately after the `python-multipart>=0.0.9` line:

```
PyJWT>=2.8.0
bcrypt>=4.1.0
```

- [ ] **Step 2: Add the two settings fields**

In `backend/app/core/settings.py`, inside `class Settings`, immediately after the `dnse_api_version` line:

```python
    # Auth — see docs/superpowers/specs/2026-09-01-simple-auth-design.md.
    # No default for the key on purpose: a committed fallback secret is how
    # tokens become forgeable. app/core/security.py decides what to do when it
    # is empty (refuse to start in production, ephemeral key in development).
    auth_secret_key: str = os.getenv("APP_AUTH_SECRET_KEY", "")
    auth_token_ttl_days: int = int(os.getenv("APP_AUTH_TOKEN_TTL_DAYS", "30"))
```

- [ ] **Step 3: Write the failing tests**

Create `backend/tests/test_auth.py`:

```python
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
    forged = jwt.encode({"sub": "phuc"}, "a-different-secret", algorithm=ALGORITHM)
    with pytest.raises(TokenError):
        decode_access_token(forged)


def test_token_without_a_subject_is_rejected():
    empty = jwt.encode({"foo": "bar"}, security._SECRET_KEY, algorithm=ALGORITHM)
    with pytest.raises(TokenError):
        decode_access_token(empty)


def test_garbage_input_raises_rather_than_returning_something_truthy():
    with pytest.raises(TokenError):
        decode_access_token("not.a.token")
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_auth.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'app.core.security'`

- [ ] **Step 5: Write the implementation**

Create `backend/app/core/security.py`:

```python
"""Password hashing and JWT minting.

Deliberately free of FastAPI and SQLAlchemy imports so it can be tested
without an app or a database. ``bcrypt`` is used directly rather than through
``passlib``: passlib is effectively unmaintained and its bcrypt-4.x backend
detection is a known source of spurious warnings and breakage.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from loguru import logger

from app.core.settings import settings

ALGORITHM = "HS256"

# bcrypt hashes at most 72 bytes and silently ignores the rest, which would
# make two different long passwords interchangeable.
_MAX_PASSWORD_BYTES = 72


class TokenError(Exception):
    """A token could not be decoded: malformed, expired, or wrongly signed."""


def _resolve_secret_key() -> str:
    """The HS256 signing key, or a loud ephemeral stand-in outside production."""
    if settings.auth_secret_key:
        return settings.auth_secret_key
    if settings.environment == "production":
        raise RuntimeError(
            "APP_AUTH_SECRET_KEY is not set. Refusing to start in production "
            "with an ephemeral signing key — every restart would log everyone "
            "out, and there is no committed default on purpose."
        )
    logger.warning(
        "APP_AUTH_SECRET_KEY is not set — signing tokens with an ephemeral key. "
        "Every restart invalidates all sessions. Set it in .env to persist logins."
    )
    return secrets.token_urlsafe(48)


# Resolved once per process: regenerating per call would mean no token ever
# verified against the key that signed it.
_SECRET_KEY = _resolve_secret_key()


def hash_password(password: str) -> str:
    """Return a salted bcrypt digest of ``password``."""
    if not password:
        raise ValueError("password must not be empty")
    encoded = password.encode("utf-8")
    if len(encoded) > _MAX_PASSWORD_BYTES:
        raise ValueError(
            f"password must be at most {_MAX_PASSWORD_BYTES} bytes; bcrypt "
            "ignores anything beyond that"
        )
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Whether ``password`` matches ``password_hash``.

    A malformed or truncated stored hash reads as "no" rather than raising —
    a corrupt column should fail the login, not 500 the endpoint.
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(
    subject: str, expires_delta: timedelta | None = None
) -> tuple[str, datetime]:
    """Mint a token for ``subject`` (the username). Returns (token, expiry)."""
    now = datetime.now(timezone.utc)
    expires_at = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(days=settings.auth_token_ttl_days)
    )
    token = jwt.encode(
        {"sub": subject, "iat": now, "exp": expires_at},
        _SECRET_KEY,
        algorithm=ALGORITHM,
    )
    return token, expires_at


def decode_access_token(token: str) -> str:
    """Return the token's subject, or raise ``TokenError``."""
    try:
        payload = jwt.decode(token, _SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise TokenError("token carries no subject")
    return subject
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_auth.py -v`
Expected: 12 passed. A `PYTHONWARNINGS`-level loguru warning about the ephemeral key is expected and correct — it proves the no-default branch works.

- [ ] **Step 7: Commit**

```bash
git add backend/app/core/security.py backend/app/core/settings.py \
        backend/requirements.txt backend/tests/test_auth.py
git commit -m "feat(auth): bcrypt password hashing and JWT primitives"
```

---

### Task 2: The `users` table

**Files:**
- Create: `backend/app/db/models/user.py`
- Create: `backend/alembic/versions/e2f6a70c9b41_add_users_table.py`
- Modify: `backend/app/db/base.py` (the model-import block, around line 12)

**Interfaces:**
- Consumes: `Base` from `app.db.base`.
- Produces: `User` with columns `id: int`, `username: str`, `password_hash: str`, `is_active: bool`, `created_at`.

- [ ] **Step 1: Write the model**

Create `backend/app/db/models/user.py`:

```python
from sqlalchemy import Boolean, Column, Integer, String, TIMESTAMP, text

from app.db.base import Base


class User(Base):
    """An application login. Seeded by ``app/scripts/create_user.py``.

    Carries no ownership of data: every authenticated user sees the same single
    portfolio. The row exists so one person can be revoked (``is_active``)
    without rotating everyone else's tokens.
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, server_default=text("1"))
    created_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
```

- [ ] **Step 2: Register the model so SQLAlchemy and alembic see it**

In `backend/app/db/base.py`, add to the existing import block (after the `corporate_action` line):

```python
from app.db.models.user import User
```

`alembic/env.py` needs no change: it does `from app.db.base import Base`, which executes this block.

- [ ] **Step 3: Write the migration**

Create `backend/alembic/versions/e2f6a70c9b41_add_users_table.py`:

```python
"""add users table

Revision ID: e2f6a70c9b41
Revises: b7e2f4c81d35
Create Date: 2026-09-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2f6a70c9b41"
down_revision: Union[str, None] = "b7e2f4c81d35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")
```

- [ ] **Step 4: Verify the migration chain is linear**

Run: `cd backend && python -c "
import re, pathlib
revs = {}
for f in pathlib.Path('alembic/versions').glob('*.py'):
    t = f.read_text()
    r = re.search(r'^revision(?::\s*str)?\s*=\s*[\'\"]([^\'\"]+)', t, re.M)
    d = re.search(r'^down_revision(?::\s*Union\[str, None\])?\s*=\s*[\'\"]([^\'\"]+)', t, re.M)
    if r: revs[r.group(1)] = d.group(1) if d else None
children = {}
for r, d in revs.items(): children.setdefault(d, []).append(r)
heads = [r for r in revs if r not in children]
print('heads:', heads)
print('branch points:', {k: v for k, v in children.items() if len(v) > 1})
"`

Expected: `heads: ['e2f6a70c9b41']` and `branch points: {}`. Two heads or a branch point means `down_revision` is wrong — fix it before continuing.

- [ ] **Step 5: Confirm nothing regressed**

Run: `cd backend && pytest tests`
Expected: same pass/skip counts as before this task. Importing `User` must not break model registration.

- [ ] **Step 6: Commit**

```bash
git add backend/app/db/models/user.py backend/app/db/base.py \
        backend/alembic/versions/e2f6a70c9b41_add_users_table.py
git commit -m "feat(auth): add users table and model"
```

---

### Task 3: Login and `/auth/me` routes, and the broker split

The routes land before the guard is switched on (Task 4) so there is never a
commit where the app is locked with no endpoint to log in through.

**Files:**
- Create: `backend/app/schemas/auth.py`
- Create: `backend/app/api/v1/routes/broker.py`
- Rewrite: `backend/app/api/v1/routes/auth.py`
- Modify: `backend/app/main.py` (imports around line 27, router registration around line 87)
- Modify: `frontend/src/lib/services/chat.ts:69`
- Modify: `backend/tests/test_auth.py` (append the route tests)

**Interfaces:**
- Consumes: `hash_password`, `verify_password`, `create_access_token` (Task 1); `User` (Task 2); `get_db` from `app.db.base`.
- Produces:
  - `POST /api/v1/auth/login` — body `{username, password}` → `{access_token, token_type, expires_at}`
  - `POST /api/v1/broker/refresh-token` (the relocated MBS proxy, body unchanged)
  - `LoginRequest`, `TokenResponse`, `UserOut` in `app.schemas.auth`
  - `broker_router` in `app.api.v1.routes.broker`

- [ ] **Step 1: Write the failing route tests**

Append to `backend/tests/test_auth.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_auth.py -v -k login`
Expected: FAIL with 404 on `/api/v1/auth/login` (the route does not exist yet). If MySQL is unreachable these SKIP instead — that is acceptable, but say so when reporting.

- [ ] **Step 3: Write the schemas**

Create `backend/app/schemas/auth.py`:

```python
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
```

- [ ] **Step 4: Move the MBS proxy out of `auth.py`**

Create `backend/app/api/v1/routes/broker.py` containing the **current** contents of
`auth.py` verbatim, with two changes: the module docstring below, and the
router prefix `/auth` → `/broker`.

```python
"""Proxy for the MBS broker's token refresh, used by the Chat Agents page.

This is NOT application authentication. It used to live at
``/auth/refresh-token``, one path segment away from ``/auth/login``, carrying
its own unrelated ``access_token``/``refresh_token`` vocabulary — an easy thing
to mistake for the app's own session refresh. It moved here to remove that
ambiguity; the request handling is unchanged.
"""
```

The router line becomes:

```python
router = APIRouter(prefix="/broker", tags=["broker"])
```

Everything else in the file — `REFRESH_URL`, `RefreshTokenRequest`, the
`refresh_token` handler and its `@router.post("/refresh-token")` decorator —
is copied across unmodified.

- [ ] **Step 5: Replace `auth.py` with the real thing**

Overwrite `backend/app/api/v1/routes/auth.py`:

```python
"""Application authentication: log in, and identify the caller."""
from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from sqlalchemy.orm import Session

from functools import lru_cache

from app.core.security import create_access_token, hash_password, verify_password
from app.db.base import get_db
from app.db.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])

_BAD_CREDENTIALS = "Incorrect username or password"


@lru_cache(maxsize=1)
def _timing_decoy_hash() -> str:
    """A real bcrypt digest to verify against when the username is unknown.

    Without it a missing user returns immediately while a wrong password costs
    ~250ms of bcrypt, and that difference tells an attacker which usernames
    exist. Computed on the first unknown-username login rather than at import,
    so it costs nothing at startup or on the happy path. The plaintext is
    arbitrary — this digest guards nothing.
    """
    return hash_password("unused")


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.query(User).filter(User.username == payload.username).one_or_none()

    if user is None:
        verify_password(payload.password, _timing_decoy_hash())
        logger.info("auth: login failed — no such user {!r}", payload.username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _BAD_CREDENTIALS)

    if not verify_password(payload.password, user.password_hash):
        logger.info("auth: login failed — bad password for {!r}", payload.username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _BAD_CREDENTIALS)

    if not user.is_active:
        # Same wording as a bad password: whether an account exists but is
        # disabled is not something an unauthenticated caller should learn.
        logger.info("auth: login refused — {!r} is deactivated", payload.username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _BAD_CREDENTIALS)

    token, expires_at = create_access_token(user.username)
    logger.info("auth: {!r} logged in until {}", user.username, expires_at)
    return TokenResponse(access_token=token, expires_at=expires_at)
```

`GET /auth/me` is deliberately **not** here: it depends on `require_user`, which
Task 4 creates. Adding it now would make this module unimportable.

- [ ] **Step 6: Register the broker router**

In `backend/app/main.py`, after the `auth` import near line 27:

```python
from app.api.v1.routes.broker import router as broker_router
```

and after the `app.include_router(auth_router, prefix=api_prefix)` line near 87:

```python
    app.include_router(broker_router, prefix=api_prefix)
```

- [ ] **Step 7: Point the frontend at the new broker path**

`frontend/src/lib/services/chat.ts` line 69:

```ts
): Promise<RefreshTokenResponse> => apiPost('/broker/refresh-token', payload);
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `cd backend && pytest tests`
Expected: the crypto tests pass; the five login tests pass, or all five skip with "MySQL not reachable" — report which. The rest of the suite is unchanged: routes exist but nothing is enforced yet, which is why this task is safe on its own.

- [ ] **Step 9: Commit**

```bash
git add backend/app/schemas/auth.py backend/app/api/v1/routes/auth.py \
        backend/app/api/v1/routes/broker.py backend/app/main.py \
        backend/tests/test_auth.py frontend/src/lib/services/chat.ts
git commit -m "feat(auth): login and me routes; move MBS proxy to /broker"
```

---

### Task 4: The deny-by-default guard

The task that actually closes the door. It must leave the whole existing suite green.

**Files:**
- Create: `backend/app/api/deps.py`
- Modify: `backend/app/api/v1/routes/auth.py` (append `/auth/me`)
- Modify: `backend/app/main.py` (the `FastAPI(...)` call around line 43)
- Modify: `backend/tests/conftest.py`
- Modify: `backend/pytest.ini`
- Modify: `backend/tests/test_auth.py` (append the guard tests)

**Interfaces:**
- Consumes: `decode_access_token`, `TokenError` (Task 1); `User` (Task 2); `get_db`.
- Produces: `require_user(request, credentials, db) -> User | None`, `EXEMPT_PATHS: frozenset[str]`, `bearer_scheme`, and `GET /api/v1/auth/me` → `{id, username}`.

- [ ] **Step 1: Write the failing guard tests**

Append to `backend/tests/test_auth.py`:

```python
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
    forged = jwt.encode({"sub": "phuc"}, "a-different-secret", algorithm=ALGORITHM)
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_auth.py -v -k "real_auth or guard"`
Expected: collection error — `ModuleNotFoundError: No module named 'app.api.deps'`

- [ ] **Step 3: Write the dependency**

Create `backend/app/api/deps.py`:

```python
"""The app-wide authentication guard.

Registered on the ``FastAPI`` instance itself, so every router — including any
router added later — is protected without a per-router opt-in. A dependency
rather than ASGI middleware because only a dependency can hand the resolved
``User`` to a handler, show up in OpenAPI, and be swapped out in tests with one
``dependency_overrides`` line.
"""
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger
from sqlalchemy.orm import Session

from app.core.security import TokenError, decode_access_token
from app.db.base import get_db
from app.db.models.user import User

# The only routes reachable without a token. ``/docs`` and ``/openapi.json``
# are absent because FastAPI registers them as Starlette routes, which
# app-level dependencies never touch.
EXEMPT_PATHS = frozenset(
    {
        "/api/v1/health",
        "/api/v1/auth/login",
    }
)

# auto_error=False matters twice: it lets an exempt path through without a
# header (auto_error=True raises 403 before this function runs), and it lets us
# return 401 rather than FastAPI's 403 for a missing credential.
bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    """Resolve the caller, or raise 401. Returns ``None`` on exempt paths."""
    if request.url.path.rstrip("/") in EXEMPT_PATHS:
        return None

    if credentials is None or not credentials.credentials:
        raise _unauthorized("Not authenticated")

    try:
        username = decode_access_token(credentials.credentials)
    except TokenError as exc:
        # Logged at info with the reason: expired and forged look identical
        # from the client side, and telling them apart matters when debugging.
        logger.info("auth: rejected token on {} — {}", request.url.path, exc)
        raise _unauthorized("Invalid or expired token")

    user = db.query(User).filter(User.username == username).one_or_none()
    if user is None:
        logger.info("auth: token names {!r}, which has no row", username)
        raise _unauthorized("Invalid or expired token")
    if not user.is_active:
        logger.info("auth: {!r} is deactivated", username)
        raise _unauthorized("Account is deactivated")

    return user
```

- [ ] **Step 4: Add `GET /auth/me`**

Now that `require_user` exists, append to `backend/app/api/v1/routes/auth.py`:

```python
@router.get("/me", response_model=UserOut)
def read_me(user: User = Depends(require_user)) -> User:
    """The caller's own identity. Guarded, so it doubles as a token check."""
    return user
```

and extend that file's imports:

```python
from app.api.deps import require_user
from app.schemas.auth import LoginRequest, TokenResponse, UserOut
```

(`UserOut` joins the existing `from app.schemas.auth import ...` line; `Depends`
is already imported from `fastapi`.)

- [ ] **Step 5: Switch the guard on**

In `backend/app/main.py`, change the `FastAPI(...)` construction (around line 43):

```python
    # Deny by default: every router mounted below is guarded, including any
    # added later. app/api/deps.py holds the exempt list.
    app = FastAPI(
        title=settings.project_name,
        version="0.1.0",
        dependencies=[Depends(require_user)],
    )
```

Add to the imports at the top:

```python
from fastapi import Depends, FastAPI, Request

from app.api.deps import require_user
```

(The existing line is `from fastapi import FastAPI, Request` — extend it rather than adding a second import.)

- [ ] **Step 6: Keep the existing suite green**

In `backend/tests/conftest.py`, add at the end:

```python
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
```

The imports are inside the fixture on purpose: `conftest.py` runs a MySQL
reachability probe at import time, and importing `app.main` at module scope
would pull the whole application in before that probe.

- [ ] **Step 7: Register the marker**

Replace `backend/pytest.ini` with:

```ini
[pytest]
asyncio_mode=auto
markers =
    real_auth: exercise the real authentication guard instead of the stubbed user
```

- [ ] **Step 8: Run the full suite**

Run: `cd backend && pytest tests`
Expected: every previously-passing test still passes, plus the new auth tests. This is the step that proves deny-by-default did not break the app. If any pre-existing test now 401s, the autouse fixture is not applying — fix that rather than editing the test.

- [ ] **Step 9: Commit**

```bash
git add backend/app/api/deps.py backend/app/main.py backend/tests/conftest.py \
        backend/pytest.ini backend/tests/test_auth.py
git commit -m "feat(auth): guard every route by default"
```

---

### Task 5: The user-provisioning CLI

**Files:**
- Create: `backend/app/scripts/__init__.py`
- Create: `backend/app/scripts/create_user.py`

**Interfaces:**
- Consumes: `hash_password` (Task 1); `User` (Task 2); `SessionLocal` from `app.db.base`.
- Produces: `python -m app.scripts.create_user <username> [--deactivate] [--activate]`

- [ ] **Step 1: Create the package marker**

```bash
cd backend && touch app/scripts/__init__.py
```

- [ ] **Step 2: Write the CLI**

Create `backend/app/scripts/create_user.py`:

```python
"""Create a user, reset a password, or revoke access.

    python -m app.scripts.create_user phuc
    python -m app.scripts.create_user phuc --deactivate
    python -m app.scripts.create_user phuc --activate

Lives under ``app/`` rather than ``backend/scripts/`` because the Docker image
copies only ``app``, ``tasks``, ``alembic`` and ``libs`` — a file in
``backend/scripts/`` would not exist in the container.

The password is prompted, never accepted as an argument: an argv password lands
in shell history and in the process list.
"""
from __future__ import annotations

import argparse
import getpass
import sys

from app.core.security import hash_password
from app.db.base import SessionLocal
from app.db.models.user import User


def _prompt_for_password() -> str:
    password = getpass.getpass("Password: ")
    if not password:
        print("error: password must not be empty", file=sys.stderr)
        raise SystemExit(1)
    if password != getpass.getpass("Confirm:  "):
        print("error: passwords do not match", file=sys.stderr)
        raise SystemExit(1)
    return password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a user, reset their password, or revoke access."
    )
    parser.add_argument("username")
    flags = parser.add_mutually_exclusive_group()
    flags.add_argument(
        "--deactivate",
        action="store_true",
        help="revoke access; existing tokens stop working immediately",
    )
    flags.add_argument(
        "--activate", action="store_true", help="restore a deactivated user"
    )
    args = parser.parse_args(argv)

    session = SessionLocal()
    try:
        user = (
            session.query(User).filter(User.username == args.username).one_or_none()
        )

        if args.deactivate or args.activate:
            if user is None:
                print(f"error: no user {args.username!r}", file=sys.stderr)
                return 1
            user.is_active = args.activate
            session.commit()
            state = "activated" if args.activate else "deactivated"
            print(f"{state} {user.username!r} (id={user.id})")
            return 0

        try:
            digest = hash_password(_prompt_for_password())
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        if user is None:
            user = User(username=args.username, password_hash=digest, is_active=True)
            session.add(user)
            session.commit()
            print(f"created {user.username!r} (id={user.id})")
        else:
            user.password_hash = digest
            user.is_active = True
            session.commit()
            print(f"updated password for {user.username!r} (id={user.id})")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Verify it is importable and its help renders**

Run: `cd backend && python -m app.scripts.create_user --help`
Expected: the usage text, listing `username`, `--deactivate`, `--activate`. This runs no queries, so it works without MySQL.

- [ ] **Step 4: Confirm nothing regressed**

Run: `cd backend && pytest tests`
Expected: unchanged from Task 4.

- [ ] **Step 5: Commit**

```bash
git add backend/app/scripts/__init__.py backend/app/scripts/create_user.py
git commit -m "feat(auth): CLI to create, update and revoke users"
```

---

### Task 6: Token storage and the fetch interceptor

The riskiest file in the change. The origin check must not attach the token to third-party requests.

**Files:**
- Create: `frontend/src/lib/auth/token.ts`
- Create: `frontend/src/lib/auth/authFetch.ts`
- Modify: `frontend/src/main.tsx`

**Interfaces:**
- Consumes: `API_BASE_URL` from `frontend/src/lib/api.ts`.
- Produces:
  - `getToken(): string | null`, `setToken(token: string): void`, `clearToken(): void`
  - `UNAUTHORIZED_EVENT = 'auth:unauthorized'`
  - `isApiUrl(rawUrl: string): boolean`
  - `installAuthFetch(): void`

- [ ] **Step 1: Write the token store**

Create `frontend/src/lib/auth/token.ts`:

```ts
const TOKEN_KEY = 'auth_token';

/** Dispatched on `window` when the API rejects our token. */
export const UNAUTHORIZED_EVENT = 'auth:unauthorized';

/**
 * Every accessor is wrapped: localStorage throws outright in a Safari private
 * window and wherever site data is blocked, and a storage failure must not take
 * the whole app down with it.
 */
export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Session lasts only as long as the tab. Better than refusing to log in.
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Nothing to do; the token was never persisted.
  }
}
```

- [ ] **Step 2: Write the interceptor**

Create `frontend/src/lib/auth/authFetch.ts`:

```ts
import { API_BASE_URL } from '../api';
import { UNAUTHORIZED_EVENT, clearToken, getToken } from './token';

/**
 * Wraps `window.fetch` so API requests carry the bearer token.
 *
 * Why patch the global instead of changing `apiGet`/`apiPost`: those two see
 * only a fraction of API traffic. Roughly 76 raw `fetch()` calls across 18
 * files talk to the API directly — all of `lib/services/timeseries.ts`,
 * `quote.ts`, `chat.ts`, `tradingAgents.ts`, `mvf.ts`, the portfolio CRUD
 * components, `pages/Home.tsx`. Threading a helper through every one of them is
 * a wide diff that can silently miss a call site, including any added later.
 *
 * The origin check is the load-bearing part. It must match our API and nothing
 * else: attaching the token to the TradingView CDN or any other third party
 * would hand it out. It is deliberately narrow — an absolute URL under
 * API_BASE_URL, or a same-origin `/api/...` path.
 */
function apiPrefix(): string {
  return new URL(API_BASE_URL, window.location.origin).toString();
}

export function isApiUrl(rawUrl: string): boolean {
  let resolved: URL;
  try {
    resolved = new URL(rawUrl, window.location.origin);
  } catch {
    return false;
  }

  if (resolved.toString().startsWith(apiPrefix())) return true;

  // `SectorPerformanceChart.tsx` fetches a bare `/api/v1/...` path, which only
  // resolves behind nginx rather than through API_BASE_URL.
  return (
    resolved.origin === window.location.origin &&
    resolved.pathname.startsWith('/api/')
  );
}

function urlOf(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

export function installAuthFetch(): void {
  const original = window.fetch.bind(window);

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = urlOf(input);
    if (!isApiUrl(url)) return original(input, init);

    const headers = new Headers(
      init?.headers ?? (input instanceof Request ? input.headers : undefined),
    );
    const token = getToken();
    if (token && !headers.has('Authorization')) {
      headers.set('Authorization', `Bearer ${token}`);
    }

    const response = await original(input, { ...init, headers });

    // A 401 from the login endpoint is just a wrong password — the form shows
    // it. Anywhere else it means our token died, so drop it and let
    // AuthProvider redirect.
    if (response.status === 401 && !url.includes('/auth/login')) {
      clearToken();
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    }

    return response;
  };
}
```

- [ ] **Step 3: Install it before the first render**

Rewrite `frontend/src/main.tsx`:

```tsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { installAuthFetch } from './lib/auth/authFetch';
import './styles/global.css';

// Before the first render: a component that fetches on mount must already see
// the wrapped fetch.
installAuthFetch();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

- [ ] **Step 4: Verify types and lint**

Run: `cd frontend && npm run build`
Expected: succeeds. `npm run lint` — expected: zero warnings.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/auth/token.ts frontend/src/lib/auth/authFetch.ts \
        frontend/src/main.tsx
git commit -m "feat(auth): attach bearer token to API requests via fetch interceptor"
```

---

### Task 7: Login page and route guard

**Files:**
- Create: `frontend/src/lib/services/auth.ts`
- Create: `frontend/src/components/auth/AuthProvider.tsx`
- Create: `frontend/src/components/auth/RequireAuth.tsx`
- Create: `frontend/src/pages/Login.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `getToken`/`setToken`/`clearToken`/`UNAUTHORIZED_EVENT` (Task 6); `apiGet`/`apiPost` from `lib/api.ts`; `POST /auth/login` and `GET /auth/me` (Task 3).
- Produces:
  - `login(username, password): Promise<LoginResponse>`, `fetchMe(): Promise<AuthUser>`
  - `useAuth(): { user: AuthUser | null; status: AuthStatus; signIn(u, p): Promise<void>; signOut(): void }`
  - `type AuthStatus = 'loading' | 'authed' | 'anon'`
  - `<AuthProvider>`, `<RequireAuth>`, `<Login />`

- [ ] **Step 1: Write the service calls**

Create `frontend/src/lib/services/auth.ts`:

```ts
import { apiGet, apiPost } from '../api';

export type AuthUser = {
  id: number;
  username: string;
};

export type LoginResponse = {
  access_token: string;
  token_type: string;
  expires_at: string;
};

export const login = (username: string, password: string) =>
  apiPost<LoginResponse>('/auth/login', { username, password });

export const fetchMe = () => apiGet<AuthUser>('/auth/me');
```

- [ ] **Step 2: Write the provider**

Create `frontend/src/components/auth/AuthProvider.tsx`:

```tsx
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { UNAUTHORIZED_EVENT, clearToken, getToken, setToken } from '../../lib/auth/token';
import { fetchMe, login, type AuthUser } from '../../lib/services/auth';

export type AuthStatus = 'loading' | 'authed' | 'anon';

type AuthContextValue = {
  user: AuthUser | null;
  status: AuthStatus;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>');
  return value;
}

export default function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthStatus>(getToken() ? 'loading' : 'anon');

  const signOut = useCallback(() => {
    clearToken();
    setUser(null);
    setStatus('anon');
    // Otherwise the next person to log in sees the previous user's cached
    // portfolio flash on screen before the refetch lands.
    queryClient.clear();
  }, [queryClient]);

  // A stored token proves nothing: it may be expired, or its user deactivated.
  // /auth/me is the cheapest way to ask the server.
  useEffect(() => {
    if (!getToken()) return;
    let cancelled = false;
    fetchMe()
      .then((me) => {
        if (cancelled) return;
        setUser(me);
        setStatus('authed');
      })
      .catch(() => {
        if (cancelled) return;
        clearToken();
        setUser(null);
        setStatus('anon');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // The interceptor fires this when any API call comes back 401.
  useEffect(() => {
    const onUnauthorized = () => signOut();
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [signOut]);

  const signIn = useCallback(async (username: string, password: string) => {
    const { access_token } = await login(username, password);
    setToken(access_token);
    const me = await fetchMe();
    setUser(me);
    setStatus('authed');
  }, []);

  const value = useMemo(
    () => ({ user, status, signIn, signOut }),
    [user, status, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
```

- [ ] **Step 3: Write the guard**

Create `frontend/src/components/auth/RequireAuth.tsx`:

```tsx
import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { Box, CircularProgress } from '@mui/material';

import { useAuth } from './AuthProvider';

export default function RequireAuth({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const location = useLocation();

  if (status === 'loading') {
    return (
      <Box
        sx={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <CircularProgress size={28} />
      </Box>
    );
  }

  if (status === 'anon') {
    // `from` lets the login page send them back where they were headed.
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <>{children}</>;
}
```

- [ ] **Step 4: Write the login page**

Create `frontend/src/pages/Login.tsx`:

```tsx
import { useState } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Stack,
  TextField,
  Typography,
} from '@mui/material';

import { useAuth } from '../components/auth/AuthProvider';

type LocationState = { from?: { pathname?: string } };

export default function Login() {
  const { status, signIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const from = (location.state as LocationState | null)?.from?.pathname ?? '/';

  if (status === 'authed') return <Navigate to={from} replace />;

  const onSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await signIn(username, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign in failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Box
      sx={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        p: 2,
      }}
    >
      <Card sx={{ width: '100%', maxWidth: 360 }}>
        <CardContent>
          <Typography variant="h6" sx={{ mb: 0.5 }}>
            Quant Terminal
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2.5 }}>
            Sign in to continue
          </Typography>

          <form onSubmit={onSubmit}>
            <Stack spacing={2}>
              <TextField
                label="Username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoFocus
                fullWidth
                size="small"
              />
              <TextField
                label="Password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                fullWidth
                size="small"
              />
              {error && <Alert severity="error">{error}</Alert>}
              <Button
                type="submit"
                variant="contained"
                disabled={submitting || !username || !password}
                fullWidth
              >
                {submitting ? 'Signing in…' : 'Sign in'}
              </Button>
            </Stack>
          </form>
        </CardContent>
      </Card>
    </Box>
  );
}
```

- [ ] **Step 5: Restructure the routes**

In `frontend/src/App.tsx`:

Add the imports:

```tsx
import AuthProvider from './components/auth/AuthProvider';
import RequireAuth from './components/auth/RequireAuth';
import Login from './pages/Login';
```

Rename the existing `AppRoutes` to `ShellRoutes` — its body is unchanged, it
keeps `AppShell`, `ErrorBoundary` and the full `<Routes>` table exactly as
they are. Then add a new `AppRoutes` above it:

```tsx
function AppRoutes() {
  return (
    <Routes>
      {/* Outside AppShell on purpose: no nav chrome on the login screen. */}
      <Route path="/login" element={<Login />} />
      <Route
        path="*"
        element={
          <RequireAuth>
            <ShellRoutes />
          </RequireAuth>
        }
      />
    </Routes>
  );
}
```

And wrap `AppRoutes` in the provider tree — `AuthProvider` goes inside
`BrowserRouter` (it is unused by the router but `RequireAuth` needs both) and
inside `QueryClientProvider` (it calls `useQueryClient`):

```tsx
          <BrowserRouter>
            <AuthProvider>
              <AppRoutes />
            </AuthProvider>
          </BrowserRouter>
```

Note: `ShellRoutes` still calls `useLocation` for `ErrorBoundary`'s `resetKey`,
which remains valid — it is rendered inside `BrowserRouter`.

- [ ] **Step 6: Verify types and lint**

Run: `cd frontend && npm run build && npm run lint`
Expected: both succeed, zero warnings.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/services/auth.ts frontend/src/components/auth \
        frontend/src/pages/Login.tsx frontend/src/App.tsx
git commit -m "feat(auth): login page and route guard"
```

---

### Task 8: Username and logout in the app bar

**Files:**
- Create: `frontend/src/components/layout/UserMenu.tsx`
- Modify: `frontend/src/components/layout/AppShell.tsx` (the toolbar, around line 186)

**Interfaces:**
- Consumes: `useAuth` (Task 7).
- Produces: `<UserMenu />`

- [ ] **Step 1: Write the menu**

Create `frontend/src/components/layout/UserMenu.tsx`:

```tsx
import { useState } from 'react';
import { IconButton, Menu, MenuItem, ListItemIcon, Typography } from '@mui/material';
import AccountCircleIcon from '@mui/icons-material/AccountCircle';
import LogoutIcon from '@mui/icons-material/Logout';

import { useAuth } from '../auth/AuthProvider';

export default function UserMenu() {
  const { user, signOut } = useAuth();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);

  if (!user) return null;

  return (
    <>
      <IconButton
        size="small"
        onClick={(e) => setAnchor(e.currentTarget)}
        aria-label={`Account: ${user.username}`}
      >
        <AccountCircleIcon fontSize="small" />
      </IconButton>
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
      >
        <MenuItem disabled sx={{ opacity: '1 !important' }}>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>
            {user.username}
          </Typography>
        </MenuItem>
        <MenuItem
          onClick={() => {
            setAnchor(null);
            signOut();
          }}
        >
          <ListItemIcon>
            <LogoutIcon fontSize="small" />
          </ListItemIcon>
          Sign out
        </MenuItem>
      </Menu>
    </>
  );
}
```

`signOut` sets status to `anon`, and the already-mounted `RequireAuth`
redirects to `/login`. No manual navigation needed.

- [ ] **Step 2: Slot it into the toolbar**

In `frontend/src/components/layout/AppShell.tsx`, add the import alongside the
other layout imports:

```tsx
import UserMenu from './UserMenu';
```

and place it after `<ModeToggle />` in the toolbar (around line 186):

```tsx
            <ModeToggle />
            <UserMenu />
```

- [ ] **Step 3: Verify types and lint**

Run: `cd frontend && npm run build && npm run lint`
Expected: both succeed, zero warnings.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/layout/UserMenu.tsx \
        frontend/src/components/layout/AppShell.tsx
git commit -m "feat(auth): username and sign out in the app bar"
```

---

### Task 9: Wire up the environment and verify end to end

**Files:**
- Modify: `.env` (append only — never read or echo it)
- Modify: `.env.example`
- Modify: `backend/requirements.lock.txt` (generated)
- Modify: `AGENTS.md`

- [ ] **Step 1: Generate and append the signing key**

Appends without reading the file back:

```bash
cd /Users/phuchuynh/Work/all-in-one-portfolio && \
  printf '\n# Auth — HS256 signing key for app login tokens. Rotating it logs everyone out.\nAPP_AUTH_SECRET_KEY=%s\n' \
  "$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')" >> .env && \
  echo "appended APP_AUTH_SECRET_KEY ($(grep -c APP_AUTH_SECRET_KEY .env) occurrence)"
```

Expected: `appended APP_AUTH_SECRET_KEY (1 occurrence)`. If it reports 2, the
key was appended twice — remove the duplicate, because the last one wins and
that is confusing rather than broken.

- [ ] **Step 2: Document it in the tracked example file**

Append to `.env.example` (no real value — this file is committed):

```
# Auth — HS256 signing key for app login tokens. Generate with:
#   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
# Required in production; without it the backend refuses to start.
APP_AUTH_SECRET_KEY=
APP_AUTH_TOKEN_TTL_DAYS=30
```

- [ ] **Step 3: Regenerate the dependency lock**

Run: `make lock-backend`
Expected: `backend/requirements.lock.txt` gains `pyjwt==` and `bcrypt==` pins.
Verify: `grep -iE "^(pyjwt|bcrypt)==" backend/requirements.lock.txt` prints two lines.

If `make lock-backend` is unavailable in this environment, say so plainly and
stop rather than hand-editing the lock — a hand-written pin is how the image
and the local environment drift apart.

- [ ] **Step 4: Run the full backend suite**

Run: `cd backend && pytest tests`
Expected: all pass. Report the exact counts, and name any skips (MySQL-gated
tests skip when the server at `192.168.1.3:3306` is unreachable — that is a
real gap in coverage and must be reported as one, not glossed over).

- [ ] **Step 5: Run the frontend checks**

Run: `cd frontend && npm run build && npm run lint`
Expected: both succeed, zero warnings.

- [ ] **Step 6: Apply the migration and create the first user**

```bash
make up
docker compose exec backend alembic upgrade head
docker compose exec backend python -m app.scripts.create_user phuc
```

Expected: `alembic upgrade head` reports running `e2f6a70c9b41`, and
`create_user` prints `created 'phuc' (id=1)`.

- [ ] **Step 7: Verify the guard by hand**

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/api/v1/health
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/api/v1/portfolio/positions
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/docs
```

Expected: `200`, `401`, `200` in that order.

- [ ] **Step 8: Verify the interceptor's origin check in the browser**

This is the one piece with no automated test, and the one that leaks the token
if it is wrong. In the app at http://localhost:5173:

1. Sign in as `phuc`.
2. Open DevTools → Network, then visit `/chart` (TradingView) and `/sector`.
3. Confirm requests to the API origin carry an `Authorization: Bearer` header.
4. Confirm requests to the TradingView CDN and to `/experiment-data/...` carry
   **no** `Authorization` header.

Step 4 failing means `isApiUrl` matches too broadly — fix it before shipping.

- [ ] **Step 9: Verify the session lifecycle in the browser**

1. Reload the page — you stay signed in (the token survives in localStorage).
2. Click the account icon → Sign out — you land on `/login`.
3. Visit `/portfolio` while signed out — you are redirected to `/login`, and
   after signing in you arrive at `/portfolio`, not `/`.
4. In DevTools, run `localStorage.setItem('auth_token', 'garbage')` and reload
   — you land on `/login` rather than an error card.

- [ ] **Step 10: Document the new setup step**

In `AGENTS.md`, under "Running the stack", after the API/frontend URL bullets:

```markdown
- **Auth:** every route except `/api/v1/health` and `/api/v1/auth/login` needs
  `Authorization: Bearer <token>`. `APP_AUTH_SECRET_KEY` must be set (the
  backend refuses to start without it in production). Create a login with
  `docker compose exec backend python -m app.scripts.create_user <username>`;
  revoke one with `--deactivate`. Backend tests run as a stubbed logged-in user
  via an autouse fixture in `tests/conftest.py`; a test that wants the real
  guard marks itself `@pytest.mark.real_auth`.
```

- [ ] **Step 11: Commit**

```bash
git add .env.example backend/requirements.lock.txt AGENTS.md
git commit -m "chore(auth): lock deps, document setup and env vars"
```

`.env` is git-ignored and must not appear in this commit — confirm with
`git status --short` before committing.

---

## Production rollout — owner only, not part of this work

`prod.env` needs its own `APP_AUTH_SECRET_KEY`, a **different** value from the
development one. The migration and user creation must be repeated against
production. No `make prod-*` command is run as part of implementing this plan.

## Known loose end, deliberately not addressed

`settings.backend_cors_origins` ends with `"*"` while `allow_credentials=True`
— a pairing browsers reject for credentialed requests. Harmless here because
authentication travels in a header rather than a cookie. Replacing the wildcard
with the explicit list already present above it is sensible hardening, but it
is a separate change.

There is also no login rate limiting. Bcrypt's cost factor makes online
guessing slow and the user set is a handful of known people; the timing decoy
in `auth.py` closes username enumeration. Deferred knowingly.
