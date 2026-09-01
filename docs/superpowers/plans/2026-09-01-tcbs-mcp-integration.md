# TCBS MCP Data Tier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put TCBS's first-party broker data in front of the scraped sources behind the TradingAgents analysts, for fundamentals, statements, news and insider dealing.

**Architecture:** A host-side CLI performs a one-time OAuth login and stores tokens in MySQL. A sync-facing MCP client reads those tokens and talks to TCBS's remote MCP server. A new `tcbs_tiers` module renders each tool's TCBS block, and the existing tools in `vn_data.py` call it as their top tier through `_best_effort`, keeping today's source as the fallback. The `vendor/TradingAgents` submodule is not touched.

**Tech Stack:** Python 3.11, `mcp` SDK (streamable HTTP client), SQLAlchemy 2.x + Alembic, MySQL, pytest.

**Spec:** `docs/superpowers/specs/2026-09-01-tcbs-mcp-integration-design.md`

## Global Constraints

- **Zero diff in `vendor/TradingAgents`.** Every change lands in `backend/`. The submodule is a fork tracking `upstream/main`; a diff there costs a rebase conflict forever.
- **No unit test touches the network.** The one integration test is marked and skips without a token.
- **The fallback path must stay byte-identical to current behaviour.** When TCBS is absent, disabled, or failing, every tool returns exactly what it returns today. This is the regression guard.
- **Ticker-scoped tools only.** No TCBS tool that returns the authenticated user's portfolio, holdings, or transaction history may be called. Personal financial data must not enter an agent's context.
- **Every TCBS call goes through `_best_effort`** (`backend/app/services/tradingagents/vn_data.py:166`). A failure degrades a tier; it never fails a run.
- Endpoint: `https://mcp.tcbs.com.vn/mcp/tcinvest/`
- Authorization server: `https://mcp.tcbs.com.vn/tcinvest` — discovered by following the `WWW-Authenticate` header, **never** by guessing `/.well-known/oauth-authorization-server` at the root, which advertises different endpoints with no registration and no refresh grant.
- Config vars, all optional: `TCBS_MCP_URL`, `TCBS_TIMEOUT` (default 30), `TCBS_CACHE_TTL` (default 900), `TCBS_ENABLED` (set `0` to disable).
- Run all commands from `backend/`. Tests: `python -m pytest`.

---

## File Structure

**Create:**
- `backend/app/db/models/tcbs.py` — the `tcbs_oauth_tokens` ORM model. One responsibility: the table shape.
- `backend/alembic/versions/<rev>_add_tcbs_oauth_tokens.py` — the migration.
- `backend/app/services/tcbs_token_store.py` — load/save/clear the single credential row. Isolates every other module from SQLAlchemy.
- `backend/scripts/tcbs_login.py` — host-side OAuth CLI (`login` / `status` / `logout` / `tools`).
- `backend/app/services/tcbs_mcp_client.py` — the MCP client: async→sync bridge, session, cache, refresh-on-401.
- `backend/app/services/tradingagents/tcbs_tiers.py` — renders the TCBS block for each analyst tool.
- `docs/tcbs-mcp-tools.json` — the committed `tools/list` dump.
- Tests: `backend/tests/test_tcbs_token_store.py`, `test_tcbs_login.py`, `test_tcbs_mcp_client.py`, `test_tcbs_tiering.py`.

**Modify:**
- `backend/app/db/base.py:14-17` — register the new model.
- `backend/requirements.txt` — add `mcp`.
- `backend/app/services/tradingagents/vn_data.py` — four tools gain a TCBS tier (a few lines each).

**Why `tcbs_tiers.py` rather than growing `vn_data.py`:** that file is already 1,774 lines. The spec's "changed files" listed it as the site of the tier, and it still is — the *call sites* stay there. Only the rendering moves out, so the per-tool diff stays small and reviewable.

---

### Task 1: Token store

**Files:**
- Create: `backend/app/db/models/tcbs.py`
- Create: `backend/alembic/versions/f3a92d47c1e8_add_tcbs_oauth_tokens.py`
- Create: `backend/app/services/tcbs_token_store.py`
- Modify: `backend/app/db/base.py:17`
- Test: `backend/tests/test_tcbs_token_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `TcbsCredentials` dataclass with fields `client_id: str`, `client_secret: str | None`, `access_token: str`, `refresh_token: str | None`, `expires_at: datetime | None`
  - `tcbs_token_store.load() -> TcbsCredentials | None`
  - `tcbs_token_store.save(creds: TcbsCredentials) -> None` (upsert; there is only ever one row)
  - `tcbs_token_store.clear() -> bool` (True if a row was deleted)
  - `TcbsCredentials.is_expired(skew_seconds: int = 60) -> bool`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_tcbs_token_store.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_tcbs_token_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.tcbs_token_store'`

- [ ] **Step 3: Write the model**

Create `backend/app/db/models/tcbs.py`:

```python
from sqlalchemy import Column, DateTime, Integer, String, TIMESTAMP, text

from app.db.base import Base


class TcbsOAuthToken(Base):
    """The single TCBS MCP credential set, written by the host-side login CLI.

    One row per install: the connector authorizes one TCBS account, matching the
    app's single-portfolio design. ``id`` is pinned to 1 by the store so a second
    login overwrites rather than accumulating stale grants.

    The client secret and refresh token are stored as issued. The database is
    not reachable from outside the compose network, and these are no more
    sensitive than the broker credentials already in the root ``.env``.
    """

    __tablename__ = "tcbs_oauth_tokens"

    id = Column(Integer, primary_key=True, autoincrement=False)
    client_id = Column(String(255), nullable=False)
    client_secret = Column(String(255), nullable=True)
    access_token = Column(String(2048), nullable=False)
    refresh_token = Column(String(2048), nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(
        TIMESTAMP,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
    )
```

- [ ] **Step 4: Register the model**

In `backend/app/db/base.py`, after line 17 (`from app.db.models.user import User`), add:

```python
from app.db.models.tcbs import TcbsOAuthToken
```

- [ ] **Step 5: Write the migration**

Create `backend/alembic/versions/f3a92d47c1e8_add_tcbs_oauth_tokens.py`:

```python
"""add tcbs oauth tokens table

Revision ID: f3a92d47c1e8
Revises: e2f6a70c9b41
Create Date: 2026-09-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3a92d47c1e8"
down_revision: Union[str, None] = "e2f6a70c9b41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tcbs_oauth_tokens",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("client_id", sa.String(length=255), nullable=False),
        sa.Column("client_secret", sa.String(length=255), nullable=True),
        sa.Column("access_token", sa.String(length=2048), nullable=False),
        sa.Column("refresh_token", sa.String(length=2048), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("tcbs_oauth_tokens")
```

- [ ] **Step 6: Write the store**

Create `backend/app/services/tcbs_token_store.py`:

```python
"""Read and write the single TCBS MCP credential row.

The login CLI runs on the host; the code that spends the token runs in the
backend container. MySQL is the one thing both reach, which is the same reason
``backend/scripts/manage_users.py`` talks to it directly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.db.base import SessionLocal
from app.db.models.tcbs import TcbsOAuthToken

logger = logging.getLogger(__name__)

#: There is only ever one credential set, so the row is pinned rather than
#: appended: a second login must replace the first, not shadow it.
ROW_ID = 1


@dataclass
class TcbsCredentials:
    client_id: str
    client_secret: str | None
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None

    def is_expired(self, skew_seconds: int = 60) -> bool:
        """Whether the access token is spent, or close enough to it.

        An unknown expiry is *not* treated as expired: the token is used until
        the server rejects it, and the 401-refresh path recovers. Guessing here
        would burn refreshes on tokens that were still good.
        """
        if self.expires_at is None:
            return False
        expires = self.expires_at
        if expires.tzinfo is None:
            # MySQL DATETIME comes back naive; it was stored as UTC.
            expires = expires.replace(tzinfo=timezone.utc)
        return expires - timedelta(seconds=skew_seconds) <= datetime.now(timezone.utc)


def load() -> TcbsCredentials | None:
    """The stored credentials, or None when nobody has logged in."""
    session = SessionLocal()
    try:
        row = session.get(TcbsOAuthToken, ROW_ID)
        if row is None:
            return None
        return TcbsCredentials(
            client_id=row.client_id,
            client_secret=row.client_secret,
            access_token=row.access_token,
            refresh_token=row.refresh_token,
            expires_at=row.expires_at,
        )
    finally:
        session.close()


def save(creds: TcbsCredentials) -> None:
    """Insert or replace the credential row."""
    session = SessionLocal()
    try:
        row = session.get(TcbsOAuthToken, ROW_ID)
        if row is None:
            row = TcbsOAuthToken(id=ROW_ID)
            session.add(row)
        row.client_id = creds.client_id
        row.client_secret = creds.client_secret
        row.access_token = creds.access_token
        row.refresh_token = creds.refresh_token
        row.expires_at = creds.expires_at
        session.commit()
    finally:
        session.close()


def clear() -> bool:
    """Delete the credential row. True when one was there to delete."""
    session = SessionLocal()
    try:
        row = session.get(TcbsOAuthToken, ROW_ID)
        if row is None:
            return False
        session.delete(row)
        session.commit()
        return True
    finally:
        session.close()
```

- [ ] **Step 7: Run the tests**

Run: `cd backend && python -m pytest tests/test_tcbs_token_store.py -v`
Expected: PASS. The `@requires_mysql` tests skip if MySQL is unreachable; the three `is_expired` tests must pass regardless.

- [ ] **Step 8: Apply the migration**

Run: `cd backend && alembic upgrade head`
Expected: the `tcbs_oauth_tokens` table exists. Verify with `alembic current` showing `f3a92d47c1e8`.

- [ ] **Step 9: Commit**

```bash
git add backend/app/db/models/tcbs.py backend/app/db/base.py \
        backend/alembic/versions/f3a92d47c1e8_add_tcbs_oauth_tokens.py \
        backend/app/services/tcbs_token_store.py backend/tests/test_tcbs_token_store.py
git commit -m "feat(tcbs): credential store for the MCP connector"
```

---

### Task 2: OAuth login CLI

**Files:**
- Create: `backend/scripts/tcbs_login.py`
- Test: `backend/tests/test_tcbs_login.py`

**Interfaces:**
- Consumes: `TcbsCredentials`, `tcbs_token_store.{load,save,clear}` from Task 1.
- Produces:
  - `wellknown_url(url: str, document: str) -> str` — RFC 8414 path-insertion
  - `pkce_pair() -> tuple[str, str]` — `(verifier, challenge)`
  - `discover_auth_server(resource_url: str) -> dict` — the AS metadata document
  - `register_client(registration_endpoint: str, redirect_uri: str) -> tuple[str, str | None]`
  - `parse_callback(path: str, expected_state: str) -> str` — returns the code, raises `LoginError` on mismatch
  - `LoginError(RuntimeError)`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_tcbs_login.py`:

```python
"""The parts of the TCBS OAuth flow that can be tested without a browser."""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

# The CLI lives in scripts/, which is not a package.
_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "tcbs_login.py"
_spec = importlib.util.spec_from_file_location("tcbs_login", _PATH)
tcbs_login = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tcbs_login)


def test_wellknown_url_inserts_the_path_after_the_document():
    # RFC 8414: the well-known segment goes between the host and the path, not
    # at the end. Getting this backwards is what silently lands a client on the
    # root document, which offers neither registration nor refresh.
    assert tcbs_login.wellknown_url(
        "https://mcp.tcbs.com.vn/mcp/tcinvest", "oauth-protected-resource"
    ) == (
        "https://mcp.tcbs.com.vn/.well-known/oauth-protected-resource/mcp/tcinvest"
    )


def test_wellknown_url_for_the_authorization_server():
    assert tcbs_login.wellknown_url(
        "https://mcp.tcbs.com.vn/tcinvest", "oauth-authorization-server"
    ) == (
        "https://mcp.tcbs.com.vn/.well-known/oauth-authorization-server/tcinvest"
    )


def test_wellknown_url_handles_a_bare_origin():
    assert tcbs_login.wellknown_url(
        "https://mcp.tcbs.com.vn", "oauth-authorization-server"
    ) == "https://mcp.tcbs.com.vn/.well-known/oauth-authorization-server"


def test_pkce_challenge_matches_the_rfc_7636_vector(monkeypatch):
    # RFC 7636 appendix B: this verifier must produce this challenge.
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    monkeypatch.setattr(tcbs_login, "_new_verifier", lambda: verifier)
    got_verifier, challenge = tcbs_login.pkce_pair()
    assert got_verifier == verifier
    assert challenge == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_pkce_verifier_is_unpadded_and_long_enough():
    verifier, _ = tcbs_login.pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert "=" not in verifier


def test_parse_callback_returns_the_code():
    assert tcbs_login.parse_callback("/callback?code=abc123&state=s1", "s1") == "abc123"


def test_parse_callback_rejects_a_mismatched_state():
    # The state check is the CSRF defence; a wrong one must abort the login.
    with pytest.raises(tcbs_login.LoginError, match="state"):
        tcbs_login.parse_callback("/callback?code=abc123&state=evil", "s1")


def test_parse_callback_surfaces_a_provider_error():
    with pytest.raises(tcbs_login.LoginError, match="access_denied"):
        tcbs_login.parse_callback("/callback?error=access_denied&state=s1", "s1")


def test_parse_callback_rejects_a_response_with_no_code():
    with pytest.raises(tcbs_login.LoginError, match="no authorization code"):
        tcbs_login.parse_callback("/callback?state=s1", "s1")


def test_discover_auth_server_follows_the_resource_document(monkeypatch):
    # The resource document names its AS; the AS document is then fetched from
    # *that* URL. Guessing the root would yield the wrong endpoints.
    calls = []

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, timeout=None, headers=None):
        calls.append(url)
        if "oauth-protected-resource" in url:
            return _Resp({"authorization_servers": ["https://mcp.tcbs.com.vn/tcinvest"]})
        return _Resp({"registration_endpoint": "https://mcp.tcbs.com.vn/tcinvest/register"})

    monkeypatch.setattr(tcbs_login.requests, "get", fake_get)

    meta = tcbs_login.discover_auth_server("https://mcp.tcbs.com.vn/mcp/tcinvest")

    assert meta["registration_endpoint"] == "https://mcp.tcbs.com.vn/tcinvest/register"
    assert calls == [
        "https://mcp.tcbs.com.vn/.well-known/oauth-protected-resource/mcp/tcinvest",
        "https://mcp.tcbs.com.vn/.well-known/oauth-authorization-server/tcinvest",
    ]


def test_register_client_returns_the_issued_pair(monkeypatch):
    class _Resp:
        status_code = 201

        def raise_for_status(self):
            return None

        def json(self):
            return {"client_id": "cid-1", "client_secret": "csec-1"}

    monkeypatch.setattr(
        tcbs_login.requests, "post", lambda url, json=None, timeout=None: _Resp()
    )
    assert tcbs_login.register_client(
        "https://mcp.tcbs.com.vn/tcinvest/register", "http://127.0.0.1:9999/callback"
    ) == ("cid-1", "csec-1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_tcbs_login.py -v`
Expected: FAIL — `FileNotFoundError` on `scripts/tcbs_login.py`.

- [ ] **Step 3: Write the CLI**

Create `backend/scripts/tcbs_login.py`:

```python
#!/usr/bin/env python3
"""Connect this install to the TCBS MCP server (OAuth 2.0 + iOTP).

Usage:
    python backend/scripts/tcbs_login.py login
    python backend/scripts/tcbs_login.py status
    python backend/scripts/tcbs_login.py logout
    python backend/scripts/tcbs_login.py tools [--dump docs/tcbs-mcp-tools.json]

Talks to MySQL directly, like ``manage_users.py``, so it works whether or not
the stack is running — and so the token it writes is readable by the backend
container, which is where it is actually spent.

``login`` opens a browser: TCBS requires an account login plus an iOTP
confirmation, which is why this step cannot be automated. It is one-time —
the authorization server issues a refresh token, and the MCP client renews
silently from then on.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import load_dotenv  # noqa: E402

# Same as manage_users.py: the real file is at the repository root, which is
# also what docker-compose hands the backend via env_file.
load_dotenv(REPO_ROOT / ".env")
load_dotenv(BACKEND_ROOT / ".env", override=False)

import requests  # noqa: E402

MCP_URL = "https://mcp.tcbs.com.vn/mcp/tcinvest/"
HTTP_TIMEOUT = 30


class LoginError(RuntimeError):
    """The authorization flow could not be completed."""


def wellknown_url(url: str, document: str) -> str:
    """RFC 8414 well-known URL for ``url``.

    The segment is *inserted* between the host and the path:
    ``https://h/mcp/tcinvest`` -> ``https://h/.well-known/<doc>/mcp/tcinvest``.
    Appending it instead reaches the root document, which for TCBS advertises a
    different token endpoint with no registration and no refresh grant.
    """
    parts = urllib.parse.urlsplit(url)
    path = parts.path.rstrip("/")
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, f"/.well-known/{document}{path}", "", "")
    )


def _new_verifier() -> str:
    """A fresh PKCE code verifier. Seam for the RFC test vector."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def pkce_pair() -> tuple[str, str]:
    """``(verifier, challenge)`` for PKCE S256."""
    verifier = _new_verifier()
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def discover_auth_server(resource_url: str) -> dict:
    """Follow the protected-resource document to its authorization server."""
    resource_doc = requests.get(
        wellknown_url(resource_url, "oauth-protected-resource"),
        timeout=HTTP_TIMEOUT,
        headers={"Accept": "application/json"},
    )
    resource_doc.raise_for_status()
    servers = resource_doc.json().get("authorization_servers") or []
    if not servers:
        raise LoginError(f"{resource_url} names no authorization server")

    as_doc = requests.get(
        wellknown_url(servers[0], "oauth-authorization-server"),
        timeout=HTTP_TIMEOUT,
        headers={"Accept": "application/json"},
    )
    as_doc.raise_for_status()
    return as_doc.json()


def register_client(registration_endpoint: str, redirect_uri: str) -> tuple[str, str | None]:
    """Dynamic client registration. Returns ``(client_id, client_secret)``."""
    resp = requests.post(
        registration_endpoint,
        json={
            "client_name": "all-in-one-portfolio TradingAgents",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        },
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    client_id = payload.get("client_id")
    if not client_id:
        raise LoginError(f"registration returned no client_id: {payload}")
    return client_id, payload.get("client_secret")


def parse_callback(path: str, expected_state: str) -> str:
    """The authorization code from the redirect path, or raise."""
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
    if "error" in query:
        raise LoginError(
            f"authorization failed: {query['error'][0]} "
            f"({query.get('error_description', [''])[0]})"
        )
    if query.get("state", [None])[0] != expected_state:
        raise LoginError("authorization state did not match; aborting")
    code = query.get("code", [None])[0]
    if not code:
        raise LoginError("callback carried no authorization code")
    return code


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Catches the one redirect and hands the path to the waiting thread."""

    result: str | None = None

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's name
        type(self).result = self.path
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            "<html><body><h2>TCBS connected.</h2>"
            "<p>You can close this tab and return to the terminal.</p>"
            "</body></html>".encode("utf-8")
        )

    def log_message(self, *args):
        return  # keep the CLI's output clean


def _await_callback(server: http.server.HTTPServer, timeout: int = 300) -> str:
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout)
    if _CallbackHandler.result is None:
        raise LoginError(f"no redirect received within {timeout}s")
    return _CallbackHandler.result


def _exchange(meta: dict, params: dict) -> dict:
    resp = requests.post(meta["token_endpoint"], data=params, timeout=HTTP_TIMEOUT)
    if resp.status_code >= 400:
        raise LoginError(f"token endpoint returned {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _store_tokens(client_id: str, client_secret: str | None, payload: dict) -> None:
    from app.services.tcbs_token_store import TcbsCredentials, save

    expires_in = payload.get("expires_in")
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        if expires_in
        else None
    )
    save(
        TcbsCredentials(
            client_id=client_id,
            client_secret=client_secret,
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_at=expires_at,
        )
    )


def cmd_login(_args) -> int:
    _CallbackHandler.result = None
    server = http.server.HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    redirect_uri = f"http://127.0.0.1:{server.server_port}/callback"

    meta = discover_auth_server(MCP_URL.rstrip("/"))
    if "registration_endpoint" not in meta:
        raise LoginError(
            "the authorization server advertises no registration endpoint; "
            "check that discovery followed the per-resource document"
        )
    client_id, client_secret = register_client(meta["registration_endpoint"], redirect_uri)

    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    auth_url = meta["authorization_endpoint"] + "?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )

    print("Opening your browser to authorize TCBS access.")
    print("Log in, confirm, and approve the iOTP prompt.\n")
    print(f"If nothing opens, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    code = parse_callback(_await_callback(server), state)
    payload = _exchange(
        meta,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret or "",
            "code_verifier": verifier,
        },
    )
    _store_tokens(client_id, client_secret, payload)
    print("Connected. Token stored.")
    if not payload.get("refresh_token"):
        print(
            "Warning: no refresh token was issued — you will have to log in "
            "again when this one expires."
        )
    return 0


def cmd_status(_args) -> int:
    from app.services.tcbs_token_store import load

    creds = load()
    if creds is None:
        print("Not connected. Run: python backend/scripts/tcbs_login.py login")
        return 1
    print(f"Connected.  client_id: {creds.client_id}")
    print(f"Expires at:  {creds.expires_at or 'not stated'}")
    print(f"Refreshable: {'yes' if creds.refresh_token else 'no'}")
    print(f"Expired:     {'yes' if creds.is_expired() else 'no'}")
    return 0


def cmd_logout(_args) -> int:
    from app.services.tcbs_token_store import clear

    if clear():
        print("Local token deleted.")
    else:
        print("Nothing stored.")
    print(
        "\nThis only removed our copy. To revoke TCBS's grant, go to TCInvest -> "
        "avatar -> AI Connector -> HUY CHIA SE."
    )
    return 0


def cmd_tools(args) -> int:
    from app.services import tcbs_mcp_client

    tools = tcbs_mcp_client.list_tools()
    print(f"{len(tools)} tools")
    for tool in tools:
        print(f"  {tool['name']}")
    if args.dump:
        target = REPO_ROOT / args.dump
        target.write_text(json.dumps(tools, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nWrote {target}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("login").set_defaults(func=cmd_login)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("logout").set_defaults(func=cmd_logout)
    tools = sub.add_parser("tools")
    tools.add_argument("--dump", help="write the tool schemas to this path")
    tools.set_defaults(func=cmd_tools)

    args = parser.parse_args()
    try:
        return args.func(args)
    except LoginError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/test_tcbs_login.py -v`
Expected: PASS (11 tests). `cmd_tools` references Task 4's module but is only imported when that subcommand runs, so these tests do not need it.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/tcbs_login.py backend/tests/test_tcbs_login.py
git commit -m "feat(tcbs): host-side OAuth login CLI"
```

---

### Task 3: MCP client — bridge, session, list_tools

**Files:**
- Create: `backend/app/services/tcbs_mcp_client.py`
- Modify: `backend/requirements.txt`
- Test: `backend/tests/test_tcbs_mcp_client.py`

**Interfaces:**
- Consumes: `tcbs_token_store.load()` from Task 1.
- Produces:
  - `TcbsUnavailable(RuntimeError)` — no token, disabled, or the session could not be built
  - `TcbsNoData(RuntimeError)` — the call worked but the symbol has nothing
  - `run_sync(coro, timeout: float)` — submit a coroutine to the client's loop
  - `list_tools() -> list[dict]` — `[{"name", "description", "inputSchema"}, ...]`
  - `enabled() -> bool`
  - `reset()` — drop the session (tests and refresh use it)

- [ ] **Step 1: Add the dependency**

In `backend/requirements.txt`, alongside the other HTTP clients, add:

```
mcp>=1.9.0
```

Run: `cd backend && pip install 'mcp>=1.9.0'`

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_tcbs_mcp_client.py`:

```python
"""The TCBS MCP client: loop bridge, enablement, tool listing."""
from __future__ import annotations

import asyncio

import pytest

from app.services import tcbs_mcp_client as client
from app.services.tcbs_token_store import TcbsCredentials


@pytest.fixture(autouse=True)
def _clean_client(monkeypatch):
    client.reset()
    client._cache.clear()
    monkeypatch.delenv("TCBS_ENABLED", raising=False)
    yield
    client.reset()
    client._cache.clear()


def _creds() -> TcbsCredentials:
    return TcbsCredentials(
        client_id="cid",
        client_secret="csec",
        access_token="tok",
        refresh_token="ref",
        expires_at=None,
    )


def test_run_sync_executes_a_coroutine_off_the_calling_thread():
    async def work():
        await asyncio.sleep(0)
        return 42

    assert client.run_sync(work(), timeout=5) == 42


def test_run_sync_reuses_one_loop_across_calls():
    async def loop_id():
        return id(asyncio.get_running_loop())

    assert client.run_sync(loop_id(), timeout=5) == client.run_sync(loop_id(), timeout=5)


def test_run_sync_propagates_the_coroutine_exception():
    async def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        client.run_sync(boom(), timeout=5)


def test_enabled_is_false_without_a_token(monkeypatch):
    monkeypatch.setattr(client, "_load_credentials", lambda: None)
    assert client.enabled() is False


def test_enabled_is_false_when_switched_off(monkeypatch):
    monkeypatch.setenv("TCBS_ENABLED", "0")
    monkeypatch.setattr(client, "_load_credentials", lambda: _creds())
    assert client.enabled() is False


def test_enabled_is_true_with_a_token(monkeypatch):
    monkeypatch.setattr(client, "_load_credentials", lambda: _creds())
    assert client.enabled() is True


def test_list_tools_normalizes_the_sdk_objects(monkeypatch):
    class _Tool:
        def __init__(self, name):
            self.name = name
            self.description = f"does {name}"
            self.inputSchema = {"type": "object", "properties": {"ticker": {}}}

    class _Result:
        tools = [_Tool("getTickerOverview"), _Tool("getInsiderDealing")]

    async def fake_session():
        class _S:
            async def list_tools(self):
                return _Result()

        return _S()

    monkeypatch.setattr(client, "_session", fake_session)
    monkeypatch.setattr(client, "_load_credentials", lambda: _creds())

    tools = client.list_tools()

    assert [t["name"] for t in tools] == ["getTickerOverview", "getInsiderDealing"]
    assert tools[0]["inputSchema"]["properties"] == {"ticker": {}}


def test_list_tools_raises_unavailable_without_a_token(monkeypatch):
    monkeypatch.setattr(client, "_load_credentials", lambda: None)
    with pytest.raises(client.TcbsUnavailable):
        client.list_tools()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_tcbs_mcp_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.tcbs_mcp_client'`

- [ ] **Step 4: Write the client core**

Create `backend/app/services/tcbs_mcp_client.py`:

```python
"""Client for the TCBS MCP server (https://mcp.tcbs.com.vn/mcp/tcinvest/).

Read-only, ticker-scoped broker data: company overviews, ratio sets split for
banks and non-banks, statements with industry averages, insider dealing,
foreign flow, corporate events and ratings, for HOSE, HNX and UPCOM.

Two shapes worth knowing about:

  * **Async SDK, sync callers.** Everything downstream — ``route_to_vendor``
    and every tool in ``vn_data.py`` — is synchronous. One event loop runs in a
    daemon thread for the process lifetime and coroutines are submitted to it.
    Not ``asyncio.run`` per call: that would tear down the MCP session, and the
    OAuth handshake behind it, on every tool invocation.
  * **The session is long-lived and repairable.** ``reset()`` drops it; the next
    call rebuilds. That is how a 401 refresh recovers.

Configuration (all optional):

    TCBS_MCP_URL     default https://mcp.tcbs.com.vn/mcp/tcinvest/
    TCBS_TIMEOUT     per-call timeout, seconds (default 30)
    TCBS_CACHE_TTL   per-call cache TTL, seconds (default 900)
    TCBS_ENABLED     set to 0 to disable the tier outright
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from contextlib import AsyncExitStack
from typing import Any

logger = logging.getLogger(__name__)

MCP_URL = os.getenv("TCBS_MCP_URL", "https://mcp.tcbs.com.vn/mcp/tcinvest/")
TIMEOUT = float(os.getenv("TCBS_TIMEOUT", "30"))

# An analyst turn calls several tools for one symbol, and none of this data
# moves within a run. Matches money24h_client's TTL for the same reason.
CACHE_TTL_SECONDS = float(os.getenv("TCBS_CACHE_TTL", "900"))

_cache: dict[tuple, tuple[float, Any]] = {}

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()
_stack: AsyncExitStack | None = None
_session_obj: Any = None
_session_lock = threading.Lock()


class TcbsUnavailable(RuntimeError):
    """No usable connection: no token, disabled, or the session failed."""


class TcbsNoData(RuntimeError):
    """The call succeeded but TCBS has nothing for this symbol."""


def _load_credentials():
    """Seam so tests do not need a database."""
    from app.services.tcbs_token_store import load

    return load()


def enabled() -> bool:
    """Whether the TCBS tier should be attempted at all."""
    if os.getenv("TCBS_ENABLED", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    try:
        return _load_credentials() is not None
    except Exception as exc:  # noqa: BLE001 — a store failure means "no tier"
        logger.warning("TCBS credential lookup failed: %s", exc)
        return False


def _get_loop() -> asyncio.AbstractEventLoop:
    """The client's own event loop, running in a daemon thread."""
    global _loop
    with _loop_lock:
        if _loop is not None and not _loop.is_closed():
            return _loop
        loop = asyncio.new_event_loop()
        threading.Thread(
            target=loop.run_forever, name="tcbs-mcp-loop", daemon=True
        ).start()
        _loop = loop
        return loop


def run_sync(coro, timeout: float = TIMEOUT):
    """Run ``coro`` on the client's loop and wait for its result."""
    future = asyncio.run_coroutine_threadsafe(coro, _get_loop())
    return future.result(timeout=timeout)


async def _session():
    """The live MCP session, built on first use."""
    global _stack, _session_obj
    if _session_obj is not None:
        return _session_obj

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    creds = _load_credentials()
    if creds is None:
        raise TcbsUnavailable(
            "no TCBS token stored; run: python backend/scripts/tcbs_login.py login"
        )

    stack = AsyncExitStack()
    try:
        read, write, _ = await stack.enter_async_context(
            streamablehttp_client(
                MCP_URL,
                headers={"Authorization": f"Bearer {creds.access_token}"},
            )
        )
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
    except Exception:
        await stack.aclose()
        raise

    _stack, _session_obj = stack, session
    return session


def reset() -> None:
    """Drop the session so the next call rebuilds it (used by refresh)."""
    global _stack, _session_obj
    stack, _stack, _session_obj = _stack, None, None
    if stack is None:
        return
    try:
        run_sync(stack.aclose(), timeout=10)
    except Exception as exc:  # noqa: BLE001 — a torn-down session is the goal
        logger.debug("TCBS session close failed (ignored): %s", exc)


def list_tools() -> list[dict]:
    """Every tool the server exposes, as plain dicts."""
    if not enabled():
        raise TcbsUnavailable("TCBS tier is not enabled")

    async def _list():
        session = await _session()
        result = await session.list_tools()
        return [
            {
                "name": t.name,
                "description": getattr(t, "description", "") or "",
                "inputSchema": getattr(t, "inputSchema", {}) or {},
            }
            for t in result.tools
        ]

    with _session_lock:
        return run_sync(_list())
```

- [ ] **Step 5: Run the tests**

Run: `cd backend && python -m pytest tests/test_tcbs_mcp_client.py -v`
Expected: PASS (8 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/tcbs_mcp_client.py backend/tests/test_tcbs_mcp_client.py backend/requirements.txt
git commit -m "feat(tcbs): MCP session and sync bridge"
```

---

### Task 4: Log in for real and commit the tool schemas

**This task is interactive and cannot be delegated to a subagent** — it needs a human at a browser with TCBS credentials and an iOTP device.

**Files:**
- Create: `docs/tcbs-mcp-tools.json`

**Interfaces:**
- Consumes: `cmd_login`, `cmd_tools` from Task 2; `list_tools()` from Task 3.
- Produces: `docs/tcbs-mcp-tools.json` — the authoritative argument shapes every later task is written against.

- [ ] **Step 1: Log in**

Run: `python backend/scripts/tcbs_login.py login`
Expected: a browser opens; after the TCBS login and iOTP confirmation the terminal prints `Connected. Token stored.` If it warns that no refresh token was issued, stop and check that discovery followed the per-resource document — the root document's grant is not refreshable.

- [ ] **Step 2: Confirm the stored state**

Run: `python backend/scripts/tcbs_login.py status`
Expected: `Connected.`, an expiry, and `Refreshable: yes`.

- [ ] **Step 3: Dump the tool schemas**

Run: `python backend/scripts/tcbs_login.py tools --dump docs/tcbs-mcp-tools.json`
Expected: roughly 49 tool names printed, and the file written.

- [ ] **Step 4: Reconcile the dump against the plan**

Read `docs/tcbs-mcp-tools.json` and check the argument names for the tools Tasks 5–8 call:
`getInsiderDealing`, `getVolumeAndForeign`, `getTickerOverview`, `getStockRatio`, `getStockSameIndustry`, `getGeneralRating`, `getIncomeStatementForBank`, `getIncomeStatementForNonBank`, `getBalanceSheetForBank`, `getBalanceSheetForNonBank`, `getCashFlowForBank`, `getCashFlowForNonBank`, `getCashFlowAnalyze`, `getTickerActivityNews`, `getTickerEventNews`.

Later tasks assume a single `ticker` string argument. **Where the dump disagrees, the dump wins** — adjust the `call(...)` kwargs in those tasks and note the change in the commit message. If a tool needs an argument that cannot be supplied, drop that block; its tier degrades and the fallback serves.

- [ ] **Step 5: Commit**

```bash
git add docs/tcbs-mcp-tools.json
git commit -m "docs(tcbs): commit the tools/list schema dump"
```

---

### Task 5: Tool invocation — cache and refresh-on-401

**Files:**
- Modify: `backend/app/services/tcbs_mcp_client.py`
- Test: `backend/tests/test_tcbs_mcp_client.py`

**Interfaces:**
- Consumes: `_session`, `run_sync`, `enabled`, `reset`, `_cache` from Task 3.
- Produces:
  - `call(tool_name: str, **params) -> Any` — the single entry point for every tier. Returns the parsed payload (a dict or list), raises `TcbsUnavailable` or `TcbsNoData`.
  - `_refresh() -> bool`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_tcbs_mcp_client.py`:

```python
class _FakeResult:
    """Mimics the SDK's CallToolResult shape."""

    def __init__(self, payload=None, text=None, is_error=False):
        self.structuredContent = payload
        self.isError = is_error
        self.content = []
        if text is not None:
            class _Block:
                type = "text"

            block = _Block()
            block.text = text
            self.content = [block]


def _install_session(monkeypatch, handler):
    """Point the client at a fake session whose call_tool runs ``handler``."""

    class _S:
        async def call_tool(self, name, arguments):
            return handler(name, arguments)

    async def fake_session():
        return _S()

    monkeypatch.setattr(client, "_session", fake_session)
    monkeypatch.setattr(client, "_load_credentials", lambda: _creds())


def test_call_returns_the_structured_payload(monkeypatch):
    _install_session(
        monkeypatch, lambda name, args: _FakeResult(payload={"ticker": "TCB", "pe": 9.1})
    )
    assert client.call("getStockRatio", ticker="TCB") == {"ticker": "TCB", "pe": 9.1}


def test_call_falls_back_to_parsing_the_text_block(monkeypatch):
    _install_session(
        monkeypatch, lambda name, args: _FakeResult(text='{"ticker": "HPG"}')
    )
    assert client.call("getTickerOverview", ticker="HPG") == {"ticker": "HPG"}


def test_call_raises_no_data_on_an_empty_payload(monkeypatch):
    _install_session(monkeypatch, lambda name, args: _FakeResult(payload={}))
    with pytest.raises(client.TcbsNoData):
        client.call("getStockRatio", ticker="ZZZZ")


def test_call_raises_no_data_when_the_tool_reports_an_error(monkeypatch):
    _install_session(
        monkeypatch, lambda name, args: _FakeResult(text="not found", is_error=True)
    )
    with pytest.raises(client.TcbsNoData):
        client.call("getStockRatio", ticker="ZZZZ")


def test_call_caches_by_tool_and_arguments(monkeypatch):
    calls = []

    def handler(name, args):
        calls.append((name, tuple(sorted(args.items()))))
        return _FakeResult(payload={"n": len(calls)})

    _install_session(monkeypatch, handler)

    assert client.call("getStockRatio", ticker="TCB") == {"n": 1}
    assert client.call("getStockRatio", ticker="TCB") == {"n": 1}  # cached
    assert client.call("getStockRatio", ticker="HPG") == {"n": 2}  # different args
    assert len(calls) == 2


def test_call_ignores_a_stale_cache_entry(monkeypatch):
    monkeypatch.setattr(client, "CACHE_TTL_SECONDS", 0.0)
    seen = []

    def handler(name, args):
        seen.append(name)
        return _FakeResult(payload={"n": len(seen)})

    _install_session(monkeypatch, handler)
    client.call("getStockRatio", ticker="TCB")
    client.call("getStockRatio", ticker="TCB")
    assert len(seen) == 2


def test_call_refreshes_once_on_unauthorized_then_succeeds(monkeypatch):
    attempts = []

    def handler(name, args):
        attempts.append(name)
        if len(attempts) == 1:
            raise RuntimeError("HTTP 401 Unauthorized")
        return _FakeResult(payload={"ok": True})

    _install_session(monkeypatch, handler)
    refreshed = []
    monkeypatch.setattr(client, "_refresh", lambda: refreshed.append(1) or True)
    monkeypatch.setattr(client, "reset", lambda: None)

    assert client.call("getStockRatio", ticker="TCB") == {"ok": True}
    assert len(refreshed) == 1
    assert len(attempts) == 2


def test_call_gives_up_when_the_refresh_fails(monkeypatch):
    def handler(name, args):
        raise RuntimeError("HTTP 401 Unauthorized")

    _install_session(monkeypatch, handler)
    monkeypatch.setattr(client, "_refresh", lambda: False)
    monkeypatch.setattr(client, "reset", lambda: None)

    with pytest.raises(client.TcbsUnavailable, match="re-authorize"):
        client.call("getStockRatio", ticker="TCB")


def test_call_does_not_retry_a_non_auth_failure(monkeypatch):
    attempts = []

    def handler(name, args):
        attempts.append(name)
        raise RuntimeError("connection reset")

    _install_session(monkeypatch, handler)
    monkeypatch.setattr(client, "reset", lambda: None)

    with pytest.raises(client.TcbsUnavailable):
        client.call("getStockRatio", ticker="TCB")
    assert len(attempts) == 1


def test_call_raises_unavailable_when_disabled(monkeypatch):
    monkeypatch.setenv("TCBS_ENABLED", "0")
    monkeypatch.setattr(client, "_load_credentials", lambda: _creds())
    with pytest.raises(client.TcbsUnavailable):
        client.call("getStockRatio", ticker="TCB")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_tcbs_mcp_client.py -k "call_" -v`
Expected: FAIL — `AttributeError: module 'app.services.tcbs_mcp_client' has no attribute 'call'`

- [ ] **Step 3: Implement `call` and `_refresh`**

Append to `backend/app/services/tcbs_mcp_client.py`:

```python
def _payload(result: Any) -> Any:
    """The usable payload from a CallToolResult, or raise TcbsNoData.

    The server may answer with ``structuredContent`` or with a text block
    carrying JSON; both are normal, so both are read. An empty payload is "no
    data for this symbol", not a broken call — the distinction is what lets a
    tier fall back quietly instead of logging an error.
    """
    import json

    if getattr(result, "isError", False):
        raise TcbsNoData(f"TCBS reported an error: {_first_text(result)[:200]}")

    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured

    text = _first_text(result)
    if text:
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            return text
        if parsed:
            return parsed

    raise TcbsNoData("TCBS returned an empty payload")


def _first_text(result: Any) -> str:
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            return text
    return ""


def _is_auth_error(exc: BaseException) -> bool:
    """Whether ``exc`` looks like an expired or rejected token.

    Matched on the message because the SDK surfaces transport status codes as
    plain exceptions rather than a typed 401.
    """
    text = str(exc).lower()
    return "401" in text or "unauthorized" in text or "invalid_token" in text


def _refresh() -> bool:
    """Exchange the refresh token for a new access token. False if impossible."""
    import requests

    from app.services.tcbs_token_store import TcbsCredentials, save

    creds = _load_credentials()
    if creds is None or not creds.refresh_token:
        return False

    # Resolved the same way the login CLI does, so both agree on which
    # authorization server is authoritative.
    from datetime import datetime, timedelta, timezone

    meta = _auth_metadata()
    if not meta:
        return False

    resp = requests.post(
        meta["token_endpoint"],
        data={
            "grant_type": "refresh_token",
            "refresh_token": creds.refresh_token,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret or "",
        },
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        logger.warning("TCBS refresh failed (%s): %s", resp.status_code, resp.text[:200])
        return False

    payload = resp.json()
    expires_in = payload.get("expires_in")
    save(
        TcbsCredentials(
            client_id=creds.client_id,
            client_secret=creds.client_secret,
            access_token=payload["access_token"],
            # A server that rotates refresh tokens returns a new one; one that
            # does not expects the old one to keep working.
            refresh_token=payload.get("refresh_token") or creds.refresh_token,
            expires_at=(
                datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
                if expires_in
                else None
            ),
        )
    )
    logger.info("Refreshed the TCBS access token")
    return True


def _auth_metadata() -> dict | None:
    """The authorization server's metadata, via the protected-resource document."""
    import urllib.parse

    import requests

    def wellknown(url: str, document: str) -> str:
        parts = urllib.parse.urlsplit(url)
        return urllib.parse.urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                f"/.well-known/{document}{parts.path.rstrip('/')}",
                "",
                "",
            )
        )

    try:
        resource = requests.get(
            wellknown(MCP_URL.rstrip("/"), "oauth-protected-resource"), timeout=TIMEOUT
        )
        resource.raise_for_status()
        servers = resource.json().get("authorization_servers") or []
        if not servers:
            return None
        meta = requests.get(
            wellknown(servers[0], "oauth-authorization-server"), timeout=TIMEOUT
        )
        meta.raise_for_status()
        return meta.json()
    except Exception as exc:  # noqa: BLE001 — no metadata means no refresh
        logger.warning("TCBS auth metadata lookup failed: %s", exc)
        return None


def call(tool_name: str, **params) -> Any:
    """Invoke one TCBS tool. The single entry point for every tier.

    Raises :class:`TcbsNoData` when the symbol has nothing and
    :class:`TcbsUnavailable` for everything else, so a caller can tell "TCBS
    has no coverage here" from "TCBS is broken" — though both degrade to the
    fallback tier in practice.
    """
    import time

    if not enabled():
        raise TcbsUnavailable("TCBS tier is not enabled")

    key = (tool_name, tuple(sorted(params.items())))
    now = time.monotonic()
    cached = _cache.get(key)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    async def _invoke():
        session = await _session()
        return await session.call_tool(tool_name, params)

    with _session_lock:
        try:
            result = run_sync(_invoke())
        except TcbsNoData:
            raise
        except Exception as exc:  # noqa: BLE001 — classified below
            if not _is_auth_error(exc):
                raise TcbsUnavailable(f"TCBS call {tool_name} failed: {exc}") from exc
            logger.info("TCBS rejected the token on %s; refreshing", tool_name)
            reset()
            if not _refresh():
                raise TcbsUnavailable(
                    "TCBS token expired and could not be refreshed; re-authorize "
                    "with: python backend/scripts/tcbs_login.py login"
                ) from exc
            try:
                result = run_sync(_invoke())
            except Exception as retry_exc:  # noqa: BLE001
                raise TcbsUnavailable(
                    f"TCBS call {tool_name} failed after refresh: {retry_exc}"
                ) from retry_exc

    payload = _payload(result)
    _cache[key] = (now, payload)
    return payload
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && python -m pytest tests/test_tcbs_mcp_client.py -v`
Expected: PASS (18 tests).

- [ ] **Step 5: Verify against the live server**

Run:
```bash
cd backend && python -c "
from app.services import tcbs_mcp_client as c
print(c.call('getTickerOverview', ticker='TCB'))
"
```
Expected: a real payload. If the argument name is wrong, correct it from `docs/tcbs-mcp-tools.json`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/tcbs_mcp_client.py backend/tests/test_tcbs_mcp_client.py
git commit -m "feat(tcbs): tool invocation with caching and token refresh"
```

---

### Task 6: Insider dealing tier

The first tier to land, because `get_insider_transactions` has no fallback to preserve — it returns a hard-coded sentinel today, so any real data is an improvement and the whole path is proved end to end.

**Files:**
- Create: `backend/app/services/tradingagents/tcbs_tiers.py`
- Modify: `backend/app/services/tradingagents/vn_data.py:1336-1342`
- Test: `backend/tests/test_tcbs_tiering.py`

**Interfaces:**
- Consumes: `tcbs_mcp_client.{call, enabled, TcbsUnavailable, TcbsNoData}` from Tasks 3 and 5.
- Produces:
  - `tcbs_tiers.insider_transactions(symbol: str) -> str | None` — a markdown block, or None when TCBS cannot serve it
  - `tcbs_tiers._rows(payload) -> list[dict]` — normalizes the list/`{"data": [...]}`/`{"items": [...]}` envelopes

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_tcbs_tiering.py`:

```python
"""TCBS tiers: present when TCBS answers, invisible when it does not."""
from __future__ import annotations

import pytest

from app.services import tcbs_mcp_client as client
from app.services.tradingagents import tcbs_tiers


@pytest.fixture
def tcbs(monkeypatch):
    """Route tcbs_tiers at a scripted set of tool responses."""

    def install(responses: dict):
        def fake_call(tool_name, **params):
            if tool_name not in responses:
                raise client.TcbsNoData(f"no fixture for {tool_name}")
            value = responses[tool_name]
            if isinstance(value, Exception):
                raise value
            return value

        monkeypatch.setattr(tcbs_tiers.tcbs, "call", fake_call)
        monkeypatch.setattr(tcbs_tiers.tcbs, "enabled", lambda: True)

    return install


def test_rows_unwraps_the_common_envelopes():
    assert tcbs_tiers._rows([{"a": 1}]) == [{"a": 1}]
    assert tcbs_tiers._rows({"data": [{"a": 1}]}) == [{"a": 1}]
    assert tcbs_tiers._rows({"items": [{"a": 1}]}) == [{"a": 1}]
    assert tcbs_tiers._rows({"a": 1}) == [{"a": 1}]
    assert tcbs_tiers._rows(None) == []


def test_insider_block_renders_the_deals(tcbs):
    tcbs({
        "getInsiderDealing": {
            "data": [
                {
                    "dealAnnounceDate": "2026-08-14",
                    "name": "Nguyen Van A",
                    "position": "CEO",
                    "action": "Buy",
                    "dealVolume": 500000,
                },
            ]
        },
        "getVolumeAndForeign": {"data": [{"foreignBuyVolume": 1200000, "rsRank": 82}]},
    })

    block = tcbs_tiers.insider_transactions("TCB")

    assert block is not None
    assert "Nguyen Van A" in block
    assert "2026-08-14" in block
    assert "500,000" in block
    assert "TCBS" in block  # the source is always attributed


def test_insider_block_is_none_when_tcbs_has_nothing(tcbs):
    tcbs({"getInsiderDealing": client.TcbsNoData("nothing")})
    assert tcbs_tiers.insider_transactions("ZZZZ") is None


def test_insider_block_is_none_when_tcbs_is_unavailable(tcbs):
    tcbs({"getInsiderDealing": client.TcbsUnavailable("no token")})
    assert tcbs_tiers.insider_transactions("TCB") is None


def test_insider_block_is_none_when_the_tier_is_disabled(monkeypatch):
    monkeypatch.setattr(tcbs_tiers.tcbs, "enabled", lambda: False)
    assert tcbs_tiers.insider_transactions("TCB") is None


def test_insider_block_survives_a_partial_failure(tcbs):
    # Foreign flow is a bonus block; losing it must not lose the deals.
    tcbs({
        "getInsiderDealing": {"data": [{"name": "B", "action": "Sell", "dealVolume": 1}]},
        "getVolumeAndForeign": client.TcbsUnavailable("boom"),
    })
    block = tcbs_tiers.insider_transactions("TCB")
    assert block is not None and "Sell" in block


def test_vn_data_tool_uses_the_tcbs_block_when_present(monkeypatch):
    from app.services.tradingagents import vn_data

    monkeypatch.setattr(
        vn_data.tcbs_tiers, "insider_transactions", lambda sym: f"# {sym} insider block"
    )
    assert vn_data.get_insider_transactions("TCB") == "# TCB insider block"


def test_vn_data_tool_keeps_the_sentinel_when_tcbs_is_absent(monkeypatch):
    # The regression guard: with no TCBS, behaviour is byte-identical to before.
    from app.services.tradingagents import vn_data

    monkeypatch.setattr(vn_data.tcbs_tiers, "insider_transactions", lambda sym: None)
    result = vn_data.get_insider_transactions("TCB")
    assert result.startswith("INSIDER_DATA_UNAVAILABLE:")
    assert "TCB" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_tcbs_tiering.py -v`
Expected: FAIL — `ImportError: cannot import name 'tcbs_tiers'`

- [ ] **Step 3: Write the tier module**

Create `backend/app/services/tradingagents/tcbs_tiers.py`:

```python
"""TCBS blocks for the analyst tools in ``vn_data.py``.

Each function here renders one tool's TCBS tier and returns ``None`` — never
raises, never returns a sentinel — when TCBS cannot serve it. The caller in
``vn_data.py`` then falls through to the source it used before, so a checkout
with no TCBS login behaves exactly as it did.

Only ticker-scoped tools are called. The connector can also expose the
authenticated user's own portfolio and transaction history; none of that
belongs in an agent's context.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services import tcbs_mcp_client as tcbs

from .utils import fmt_count

logger = logging.getLogger(__name__)

#: Deals shown per block. The analyst needs the recent pattern, not the archive.
_INSIDER_ROWS = 15


def _rows(payload: Any) -> list[dict]:
    """Normalize TCBS's response envelopes to a list of records."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "listInsiderDealing", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
        return [payload]
    return []


def _try(tool: str, **params) -> Any:
    """Call one TCBS tool; None on any failure, so a block can degrade in parts."""
    try:
        return tcbs.call(tool, **params)
    except (tcbs.TcbsNoData, tcbs.TcbsUnavailable) as exc:
        logger.info("TCBS %s unavailable: %s", tool, exc)
        return None
    except Exception as exc:  # noqa: BLE001 — a tier must never raise
        logger.warning("TCBS %s failed: %s", tool, exc)
        return None


def _first(row: dict, *keys: str, default: Any = None) -> Any:
    """First present key. TCBS field names vary in case across tool families."""
    for key in keys:
        if row.get(key) not in (None, ""):
            return row[key]
        lowered = {k.lower(): v for k, v in row.items()}
        if lowered.get(key.lower()) not in (None, ""):
            return lowered[key.lower()]
    return default


def insider_transactions(symbol: str) -> str | None:
    """Insider dealing plus foreign flow, or None when TCBS cannot serve it."""
    if not tcbs.enabled():
        return None

    sym = str(symbol).upper()
    deals = _rows(_try("getInsiderDealing", ticker=sym))
    if not deals:
        return None

    lines = [
        f"# {sym} — insider dealing",
        "",
        "| Announced | Person | Position | Action | Volume |",
        "|---|---|---|---|---|",
    ]
    for row in deals[:_INSIDER_ROWS]:
        lines.append(
            "| {date} | {name} | {position} | {action} | {volume} |".format(
                date=_first(row, "dealAnnounceDate", "announceDate", "date", default="-"),
                name=_first(row, "name", "personName", "ownerName", default="-"),
                position=_first(row, "position", "title", default="-"),
                action=_first(row, "action", "dealType", "type", default="-"),
                volume=fmt_count(_first(row, "dealVolume", "volume", default=0)),
            )
        )
    if len(deals) > _INSIDER_ROWS:
        lines.append(f"| … ({len(deals) - _INSIDER_ROWS} older deals omitted) | | | | |")

    foreign = _rows(_try("getVolumeAndForeign", ticker=sym))
    if foreign:
        row = foreign[0]
        buy = _first(row, "foreignBuyVolume", "buyVolume")
        sell = _first(row, "foreignSellVolume", "sellVolume")
        rank = _first(row, "rsRank", "rs")
        parts = []
        if buy is not None:
            parts.append(f"foreign buy {fmt_count(buy)}")
        if sell is not None:
            parts.append(f"foreign sell {fmt_count(sell)}")
        if rank is not None:
            parts.append(f"RS rank {rank}")
        if parts:
            lines += ["", "Recent flow: " + ", ".join(parts) + "."]

    lines += [
        "",
        "Source: TCBS (TCInvest). Insider deals are as announced to the exchange; "
        "treat the announcement date, not the trade date, as the signal. Do not "
        "extrapolate beyond the rows shown.",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Wire it into the tool**

In `backend/app/services/tradingagents/vn_data.py`, add to the imports near
`from .utils import fmt_billion, fmt_count, fmt_ratio, iso_day, lookback_days`:

```python
from . import tcbs_tiers
```

Then replace `get_insider_transactions` (currently at `vn_data.py:1336-1342`):

```python
@failsafe("INSIDER_DATA_UNAVAILABLE", "insider transactions")
def get_insider_transactions(ticker: str) -> str:
    sym = str(ticker).upper()
    block = _best_effort(f"tcbs_insider[{sym}]", tcbs_tiers.insider_transactions, sym)
    if block:
        return block
    return (
        f"INSIDER_DATA_UNAVAILABLE: No insider-transaction feed is configured for "
        f"Vietnamese equities ({sym}). Do not fabricate filings."
    )
```

- [ ] **Step 5: Run the tests**

Run: `cd backend && python -m pytest tests/test_tcbs_tiering.py -v`
Expected: PASS (9 tests).

- [ ] **Step 6: Check the full suite is green**

Run: `cd backend && python -m pytest tests/ -q`
Expected: no new failures.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/tradingagents/tcbs_tiers.py \
        backend/app/services/tradingagents/vn_data.py \
        backend/tests/test_tcbs_tiering.py
git commit -m "feat(tcbs): real insider dealing in place of the sentinel"
```

---

### Task 7: Bank/non-bank resolver and the fundamentals tier

**Files:**
- Modify: `backend/app/services/tradingagents/tcbs_tiers.py`
- Modify: `backend/app/services/tradingagents/vn_data.py` (`get_fundamentals`, around `vn_data.py:1660`)
- Test: `backend/tests/test_tcbs_tiering.py`

**Interfaces:**
- Consumes: `_rows`, `_try`, `_first`, `tcbs` from Task 6; `sector_analyst.sector_tags` (existing, `sector_analyst.py:57`).
- Produces:
  - `tcbs_tiers.is_bank(symbol: str) -> bool`
  - `tcbs_tiers.fundamentals(symbol: str) -> str | None`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_tcbs_tiering.py`:

```python
def test_is_bank_reads_the_committed_sector_map(monkeypatch):
    monkeypatch.setattr(tcbs_tiers, "sector_tags", lambda sym: ["Ngân hàng"])
    tcbs_tiers.is_bank.cache_clear()
    assert tcbs_tiers.is_bank("TCB") is True


def test_is_bank_is_false_for_other_sectors(monkeypatch):
    monkeypatch.setattr(tcbs_tiers, "sector_tags", lambda sym: ["Thép"])
    tcbs_tiers.is_bank.cache_clear()
    assert tcbs_tiers.is_bank("HPG") is False


def test_is_bank_defaults_to_non_bank_when_unmapped(monkeypatch):
    # Non-bank is the safe default: it is the larger population, and the wrong
    # guess costs one degraded block, not a failed run.
    monkeypatch.setattr(tcbs_tiers, "sector_tags", lambda sym: [])
    tcbs_tiers.is_bank.cache_clear()
    assert tcbs_tiers.is_bank("XYZ") is False


def test_fundamentals_block_carries_ratios_peers_and_rating(tcbs, monkeypatch):
    monkeypatch.setattr(tcbs_tiers, "sector_tags", lambda sym: ["Ngân hàng"])
    tcbs_tiers.is_bank.cache_clear()
    tcbs({
        "getTickerOverview": {"exchange": "HOSE", "industry": "Ngân hàng", "employees": 12000},
        "getStockRatio": {"marketCap": 2.2e14, "pe": 9.1, "pb": 1.4, "roe": 0.18},
        "getStockSameIndustry": {"data": [
            {"ticker": "VCB", "pe": 15.2, "pb": 2.8, "roe": 0.20},
            {"ticker": "CTG", "pe": 10.1, "pb": 1.5, "roe": 0.17},
        ]},
        "getGeneralRating": {"stockRating": 4.1, "valuation": 3.8, "financialHealth": 4.5},
    })

    block = tcbs_tiers.fundamentals("TCB")

    assert block is not None
    assert "9.1" in block           # the ratio
    assert "VCB" in block           # the peer table
    assert "4.1" in block           # the rating
    assert "TCBS" in block


def test_fundamentals_block_is_none_without_core_ratios(tcbs):
    # Peers and ratings are enrichment; with no ratios there is no snapshot, so
    # the 24hmoney tier should serve instead of a half-empty TCBS block.
    tcbs({
        "getStockRatio": client.TcbsNoData("nothing"),
        "getTickerOverview": client.TcbsNoData("nothing"),
    })
    assert tcbs_tiers.fundamentals("ZZZZ") is None


def test_fundamentals_block_survives_missing_enrichment(tcbs, monkeypatch):
    monkeypatch.setattr(tcbs_tiers, "sector_tags", lambda sym: ["Thép"])
    tcbs_tiers.is_bank.cache_clear()
    tcbs({
        "getStockRatio": {"pe": 8.0, "pb": 1.1},
        "getStockSameIndustry": client.TcbsUnavailable("boom"),
        "getGeneralRating": client.TcbsUnavailable("boom"),
    })
    block = tcbs_tiers.fundamentals("HPG")
    assert block is not None and "8.0" in block


def test_vn_data_fundamentals_prefers_tcbs(monkeypatch):
    from app.services.tradingagents import vn_data

    monkeypatch.setattr(vn_data.tcbs_tiers, "fundamentals", lambda sym: f"# {sym} tcbs")
    assert vn_data.get_fundamentals("TCB") == "# TCB tcbs"


def test_vn_data_fundamentals_falls_back_to_money24h(monkeypatch):
    from app.services import money24h_client
    from app.services.tradingagents import vn_data

    monkeypatch.setattr(vn_data.tcbs_tiers, "fundamentals", lambda sym: None)
    monkeypatch.setattr(
        money24h_client, "fetch_company_index", lambda sym: {"pe": 7.7, "group_name": "Thép"}
    )
    result = vn_data.get_fundamentals("HPG")
    assert "fundamentals snapshot" in result
    assert "24hmoney" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_tcbs_tiering.py -k "is_bank or fundamentals" -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'is_bank'`

- [ ] **Step 3: Implement the resolver and the block**

Append to `backend/app/services/tradingagents/tcbs_tiers.py`:

```python
from functools import lru_cache

from .sector_analyst import sector_tags

#: TCBS splits every statement and ratio tool into bank and non-bank variants,
#: because the two report different line items entirely. The committed sector
#: map already tags banks, so the split costs no extra call.
_BANK_TAGS = {"ngân hàng", "ngan hang", "banking", "bank"}

#: Peers shown in the comparison table.
_PEER_ROWS = 8


@lru_cache(maxsize=2048)
def is_bank(symbol: str) -> bool:
    """Whether ``symbol`` reports as a bank.

    Non-bank is the default for an unmapped ticker: it is the far larger
    population, and a wrong guess costs one degraded block rather than a run.
    """
    for tag in sector_tags(str(symbol).upper()):
        if str(tag).strip().lower() in _BANK_TAGS:
            return True
    return False


def _fmt(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def fundamentals(symbol: str) -> str | None:
    """Valuation, peers and rating from TCBS, or None when it cannot serve."""
    if not tcbs.enabled():
        return None

    sym = str(symbol).upper()
    ratios = _rows(_try("getStockRatio", ticker=sym))
    overview = _rows(_try("getTickerOverview", ticker=sym))
    if not ratios and not overview:
        return None

    lines = [f"# {sym} — fundamentals snapshot", ""]

    if overview:
        row = overview[0]
        bits = []
        for label, keys in (
            ("Exchange", ("exchange", "exchangeName")),
            ("Industry", ("industry", "industryName", "icbName")),
            ("Employees", ("employees", "noEmployees")),
            ("Foreign ownership", ("foreignPercent", "foreignOwnership")),
        ):
            value = _first(row, *keys)
            if value is not None:
                bits.append(f"{label}: {value}")
        if bits:
            lines += ["  ·  ".join(bits), ""]

    if ratios:
        row = ratios[0]
        lines += ["| Metric | Value |", "|---|---|"]
        for label, keys, digits in (
            ("Market cap (VND)", ("marketCap", "marketcap"), 0),
            ("P/E", ("pe",), 2),
            ("P/B", ("pb",), 2),
            ("EPS (VND/share)", ("eps",), 0),
            ("Book value (VND/share)", ("bvps", "bookValue"), 0),
            ("ROE", ("roe",), 4),
            ("ROA", ("roa",), 4),
            ("EV/EBITDA", ("evEbitda", "ev_per_ebitda"), 2),
        ):
            value = _first(row, *keys)
            if value is not None:
                lines.append(f"| {label} | {_fmt(value, digits)} |")
        lines.append("")

    peers = _rows(_try("getStockSameIndustry", ticker=sym))
    if peers:
        lines += [
            "## Peers in the same industry",
            "",
            "| Ticker | P/E | P/B | ROE |",
            "|---|---|---|---|",
        ]
        for row in peers[:_PEER_ROWS]:
            lines.append(
                "| {t} | {pe} | {pb} | {roe} |".format(
                    t=_first(row, "ticker", "symbol", default="-"),
                    pe=_fmt(_first(row, "pe")),
                    pb=_fmt(_first(row, "pb")),
                    roe=_fmt(_first(row, "roe"), 4),
                )
            )
        lines.append("")

    rating = _rows(_try("getGeneralRating", ticker=sym))
    if rating:
        row = rating[0]
        bits = []
        for label, keys in (
            ("Overall", ("stockRating", "ratingGeneral")),
            ("Valuation", ("valuation",)),
            ("Financial health", ("financialHealth",)),
            ("Business model", ("businessModel",)),
            ("Business operation", ("businessOperation",)),
        ):
            value = _first(row, *keys)
            if value is not None:
                bits.append(f"{label} {_fmt(value, 1)}")
        if bits:
            lines += [
                "## TCBS rating (out of 5)",
                "",
                ", ".join(bits) + ".",
                "",
            ]

    lines.append(
        f"Source: TCBS (TCInvest), {'bank' if is_bank(sym) else 'non-bank'} "
        f"reporting basis. Call get_balance_sheet / get_income_statement / "
        f"get_cashflow for the underlying line items (freq='annual' for yearly). "
        f"Ratings are TCBS's own model, not a recommendation to act on."
    )
    return "\n".join(lines)
```

- [ ] **Step 4: Wire it into the tool**

In `backend/app/services/tradingagents/vn_data.py`, insert at the top of `get_fundamentals` (immediately after the docstring, before `sym = str(ticker).upper()`):

```python
    sym = str(ticker).upper()
    block = _best_effort(f"tcbs_fundamentals[{sym}]", tcbs_tiers.fundamentals, sym)
    if block:
        return block
```

The existing 24hmoney body follows unchanged (delete only its now-duplicated `sym = str(ticker).upper()` line).

- [ ] **Step 5: Run the tests**

Run: `cd backend && python -m pytest tests/test_tcbs_tiering.py -v`
Expected: PASS (17 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/tradingagents/tcbs_tiers.py \
        backend/app/services/tradingagents/vn_data.py \
        backend/tests/test_tcbs_tiering.py
git commit -m "feat(tcbs): fundamentals tier with peers and ratings"
```

---

### Task 8: Statements tier

**Files:**
- Modify: `backend/app/services/tradingagents/tcbs_tiers.py`
- Modify: `backend/app/services/tradingagents/vn_data.py` (`_statement_tool`, `vn_data.py:1619-1638`)
- Test: `backend/tests/test_tcbs_tiering.py`

**Interfaces:**
- Consumes: `is_bank`, `_rows`, `_try`, `_first`, `_fmt` from Tasks 6 and 7.
- Produces: `tcbs_tiers.statement(symbol: str, kind: str, freq: str) -> str | None`, where `kind` is one of `"cdkt"`, `"kqkd"`, `"lctt"` — the same keys `_STATEMENTS` uses at `vn_data.py:464`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_tcbs_tiering.py`:

```python
def test_statement_picks_the_bank_variant(tcbs, monkeypatch):
    monkeypatch.setattr(tcbs_tiers, "sector_tags", lambda sym: ["Ngân hàng"])
    tcbs_tiers.is_bank.cache_clear()
    seen = []

    def fake_call(tool_name, **params):
        seen.append(tool_name)
        if tool_name == "getIncomeStatementForBank":
            return {"data": [{"year": 2025, "quarter": 2, "operatingIncome": 1000}]}
        raise client.TcbsNoData("n/a")

    monkeypatch.setattr(tcbs_tiers.tcbs, "call", fake_call)
    monkeypatch.setattr(tcbs_tiers.tcbs, "enabled", lambda: True)

    block = tcbs_tiers.statement("TCB", "kqkd", "quarterly")

    assert "getIncomeStatementForBank" in seen
    assert "getIncomeStatementForNonBank" not in seen
    assert block is not None and "operatingIncome" in block


def test_statement_picks_the_non_bank_variant(tcbs, monkeypatch):
    monkeypatch.setattr(tcbs_tiers, "sector_tags", lambda sym: ["Thép"])
    tcbs_tiers.is_bank.cache_clear()
    seen = []

    def fake_call(tool_name, **params):
        seen.append(tool_name)
        if tool_name == "getBalanceSheetForNonBank":
            return {"data": [{"year": 2025, "quarter": 2, "totalAsset": 500}]}
        raise client.TcbsNoData("n/a")

    monkeypatch.setattr(tcbs_tiers.tcbs, "call", fake_call)
    monkeypatch.setattr(tcbs_tiers.tcbs, "enabled", lambda: True)

    block = tcbs_tiers.statement("HPG", "cdkt", "quarterly")

    assert "getBalanceSheetForNonBank" in seen
    assert block is not None and "totalAsset" in block


def test_statement_appends_the_industry_average(tcbs, monkeypatch):
    monkeypatch.setattr(tcbs_tiers, "sector_tags", lambda sym: ["Thép"])
    tcbs_tiers.is_bank.cache_clear()
    tcbs({
        "getBalanceSheetForNonBank": {"data": [{"year": 2025, "quarter": 2, "totalAsset": 500}]},
        "getBalanceSheetIndustryForNonBank": {"data": [{"year": 2025, "quarter": 2, "totalAsset": 900}]},
    })
    block = tcbs_tiers.statement("HPG", "cdkt", "quarterly")
    assert "Industry" in block and "900" in block


def test_statement_is_none_when_tcbs_has_nothing(tcbs):
    tcbs({})
    assert tcbs_tiers.statement("ZZZZ", "lctt", "quarterly") is None


def test_vn_data_statements_fall_back_to_ruatichsan(monkeypatch):
    from app.services.tradingagents import vn_data

    monkeypatch.setattr(vn_data.tcbs_tiers, "statement", lambda sym, kind, freq: None)
    monkeypatch.setattr(
        vn_data,
        "_load_statements",
        lambda ticker, freq: {
            "fiscalDates": ["2025Q1", "2025Q2"],
            "cdkt": [["Total assets", "", "", 100, 200]],
            "dataSource": "ruatichsan",
        },
    )
    result = vn_data.get_balance_sheet("HPG")
    assert "Total assets" in result and "ruatichsan" in result


def test_vn_data_statements_prefer_tcbs(monkeypatch):
    from app.services.tradingagents import vn_data

    monkeypatch.setattr(
        vn_data.tcbs_tiers, "statement", lambda sym, kind, freq: f"# {sym} {kind} tcbs"
    )
    assert vn_data.get_cashflow("HPG") == "# HPG lctt tcbs"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_tcbs_tiering.py -k statement -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'statement'`

- [ ] **Step 3: Implement the statement block**

Append to `backend/app/services/tradingagents/tcbs_tiers.py`:

```python
#: Statement key -> (TCBS tool stem, human title). The keys match _STATEMENTS in
#: vn_data.py so the two files stay legible side by side.
_STATEMENT_TOOLS: dict[str, tuple[str, str]] = {
    "cdkt": ("BalanceSheet", "Balance sheet (Cân đối kế toán)"),
    "kqkd": ("IncomeStatement", "Income statement (Kết quả kinh doanh)"),
    "lctt": ("CashFlow", "Cash flow (Lưu chuyển tiền tệ)"),
}

#: Periods rendered. Matches vn_data's default so switching tiers does not
#: change how much history an analyst sees.
_STMT_PERIODS = 12

#: Line items per period column. TCBS returns the full chart of accounts.
_STMT_FIELDS = 25


def _period_label(row: dict) -> str:
    year = _first(row, "year", "fiscalYear", default="")
    quarter = _first(row, "quarter", "fiscalQuarter")
    if quarter in (None, "", 0, 5):
        return str(year)
    return f"{year}Q{quarter}"


def statement(symbol: str, kind: str, freq: str) -> str | None:
    """One financial statement from TCBS, with the industry average appended."""
    if not tcbs.enabled() or kind not in _STATEMENT_TOOLS:
        return None

    sym = str(symbol).upper()
    stem, title = _STATEMENT_TOOLS[kind]
    variant = "ForBank" if is_bank(sym) else "ForNonBank"
    period = (
        "year"
        if str(freq or "quarterly").lower().startswith(("annual", "year"))
        else "quarter"
    )

    rows = _rows(_try(f"get{stem}{variant}", ticker=sym, period=period))
    if not rows:
        return None

    rows = rows[:_STMT_PERIODS]
    periods = [_period_label(r) for r in rows]

    # Union of the line items present, in first-seen order: the chart of
    # accounts is wide and not every period reports every item.
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key in ("year", "quarter", "fiscalYear", "fiscalQuarter", "ticker"):
                continue
            if key not in fields:
                fields.append(key)
    fields = fields[:_STMT_FIELDS]

    lines = [
        f"# {sym} — {title}",
        "",
        "| Line item | " + " | ".join(periods) + " |",
        "|---" * (len(periods) + 1) + "|",
    ]
    for field in fields:
        values = [_fmt(row.get(field), 2) for row in rows]
        if all(v == "-" for v in values):
            continue
        lines.append(f"| {field} | " + " | ".join(values) + " |")

    industry = _rows(_try(f"get{stem}Industry{variant}", ticker=sym, period=period))
    if industry:
        ind_rows = industry[:_STMT_PERIODS]
        lines += [
            "",
            f"## Industry average — {title}",
            "",
            "| Line item | " + " | ".join(_period_label(r) for r in ind_rows) + " |",
            "|---" * (len(ind_rows) + 1) + "|",
        ]
        for field in fields:
            values = [_fmt(row.get(field), 2) for row in ind_rows]
            if all(v == "-" for v in values):
                continue
            lines.append(f"| {field} | " + " | ".join(values) + " |")

    if kind == "lctt":
        analysis = _rows(_try("getCashFlowAnalyze", ticker=sym))
        if analysis:
            row = analysis[0]
            bits = [f"{k}: {_fmt(v, 2)}" for k, v in list(row.items())[:10]]
            lines += ["", "## Cash flow analysis", "", ", ".join(bits) + "."]

    lines += [
        "",
        f"Periods newest first, {period}ly, on the "
        f"{'bank' if is_bank(sym) else 'non-bank'} reporting basis. "
        f"Source: TCBS (TCInvest).",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Wire it into the tool**

In `backend/app/services/tradingagents/vn_data.py`, insert at the top of `_statement_tool` (`vn_data.py:1619`), immediately after `sym = str(ticker).upper()`:

```python
    block = _best_effort(
        f"tcbs_statement[{sym}/{key}]", tcbs_tiers.statement, sym, key, freq
    )
    if block:
        return block
```

The three public tools (`get_balance_sheet`, `get_income_statement`, `get_cashflow`) are unchanged — they already delegate here.

- [ ] **Step 5: Run the tests**

Run: `cd backend && python -m pytest tests/test_tcbs_tiering.py -v`
Expected: PASS (23 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/tradingagents/tcbs_tiers.py \
        backend/app/services/tradingagents/vn_data.py \
        backend/tests/test_tcbs_tiering.py
git commit -m "feat(tcbs): statements tier with industry averages"
```

---

### Task 9: News and corporate events tier

**Files:**
- Modify: `backend/app/services/tradingagents/tcbs_tiers.py`
- Modify: `backend/app/services/tradingagents/vn_data.py` (`_company_news`, `vn_data.py:968`)
- Test: `backend/tests/test_tcbs_tiering.py`

**Interfaces:**
- Consumes: `_rows`, `_try`, `_first` from Task 6.
- Produces: `tcbs_tiers.company_news(symbol: str, start_date: str, end_date: str) -> str | None`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_tcbs_tiering.py`:

```python
def test_company_news_renders_activity_and_events(tcbs):
    tcbs({
        "getTickerActivityNews": {"data": [
            {"title": "Q2 profit up 18%", "publishDate": "2026-07-20", "id": 11},
        ]},
        "getTickerEventNews": {"data": [
            {
                "eventName": "Cash dividend 10%",
                "exrightDate": "2026-08-05",
                "recordDate": "2026-08-06",
            },
        ]},
    })

    block = tcbs_tiers.company_news("TCB", "2026-07-01", "2026-08-10")

    assert block is not None
    assert "Q2 profit up 18%" in block
    assert "Cash dividend 10%" in block
    assert "2026-08-05" in block
    assert "TCBS" in block


def test_company_news_is_none_when_both_feeds_are_empty(tcbs):
    tcbs({})
    assert tcbs_tiers.company_news("ZZZZ", "2026-07-01", "2026-08-10") is None


def test_company_news_survives_one_feed_failing(tcbs):
    tcbs({
        "getTickerActivityNews": {"data": [{"title": "Only this", "publishDate": "2026-07-20"}]},
        "getTickerEventNews": client.TcbsUnavailable("boom"),
    })
    block = tcbs_tiers.company_news("TCB", "2026-07-01", "2026-08-10")
    assert block is not None and "Only this" in block


def test_company_news_tier_sits_below_the_knowledge_base(monkeypatch):
    # Curated research stays the top tier; TCBS must not displace it.
    from app.services.tradingagents import kb_search, vn_data

    monkeypatch.setattr(
        kb_search, "search", lambda q, symbols=None: [{"text": "curated", "score": 0.9}]
    )
    monkeypatch.setattr(
        kb_search, "format_hits", lambda title, hits: "KB BLOCK"
    )
    called = []
    monkeypatch.setattr(
        vn_data.tcbs_tiers,
        "company_news",
        lambda sym, s, e: called.append(sym) or "TCBS BLOCK",
    )

    result = vn_data._company_news("TCB", "2026-07-01", "2026-08-10")

    assert "KB BLOCK" in result
    assert called == []  # TCBS never reached


def test_company_news_tier_runs_when_the_knowledge_base_is_empty(monkeypatch):
    from app.services.tradingagents import kb_search, vn_data

    monkeypatch.setattr(kb_search, "search", lambda q, symbols=None: [])
    monkeypatch.setattr(
        vn_data.tcbs_tiers, "company_news", lambda sym, s, e: "TCBS BLOCK"
    )

    result = vn_data._company_news("TCB", "2026-07-01", "2026-08-10")

    assert "TCBS BLOCK" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_tcbs_tiering.py -k company_news -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'company_news'`

- [ ] **Step 3: Implement the news block**

Append to `backend/app/services/tradingagents/tcbs_tiers.py`:

```python
#: Headlines and events shown per block.
_NEWS_ROWS = 20
_EVENT_ROWS = 12


def _in_window(day: str, start_date: str, end_date: str) -> bool:
    """Whether an ISO-ish date string falls in the window. Unknown dates stay."""
    text = str(day or "")[:10]
    if len(text) != 10:
        return True
    return start_date <= text <= end_date


def company_news(symbol: str, start_date: str, end_date: str) -> str | None:
    """Corporate activity news and events from TCBS, or None."""
    if not tcbs.enabled():
        return None

    sym = str(symbol).upper()
    news = _rows(_try("getTickerActivityNews", ticker=sym))
    events = _rows(_try("getTickerEventNews", ticker=sym))
    if not news and not events:
        return None

    lines = [f"# {sym} — company news and corporate events (TCBS)", ""]

    shown = [
        row
        for row in news
        if _in_window(
            _first(row, "publishDate", "date", "createdAt", default=""),
            start_date,
            end_date,
        )
    ][:_NEWS_ROWS]
    if shown:
        lines += ["## Activity news", ""]
        for row in shown:
            date = _first(row, "publishDate", "date", "createdAt", default="?")
            title = _first(row, "title", "name", "newsTitle", default="(untitled)")
            summary = _first(row, "summary", "description", "shortContent", default="")
            lines.append(f"- **{str(date)[:10]}** — {title}")
            if summary:
                lines.append(f"  {str(summary)[:400]}")
        lines.append("")

    if events:
        lines += [
            "## Corporate events",
            "",
            "| Event | Ex-rights | Record date | Note |",
            "|---|---|---|---|",
        ]
        for row in events[:_EVENT_ROWS]:
            lines.append(
                "| {name} | {ex} | {record} | {note} |".format(
                    name=_first(row, "eventName", "name", "title", default="-"),
                    ex=str(_first(row, "exrightDate", "exRightDate", default="-"))[:10],
                    record=str(_first(row, "recordDate", default="-"))[:10],
                    note=str(_first(row, "eventDesc", "description", default=""))[:120],
                )
            )
        lines.append("")

    lines.append(
        "Source: TCBS (TCInvest). Corporate events are scheduled, not completed: "
        "an ex-rights date in the future is a commitment, not a result. Do not "
        "fabricate headlines beyond those listed."
    )
    return "\n".join(lines)
```

- [ ] **Step 4: Wire it into the tier stack**

In `backend/app/services/tradingagents/vn_data.py`, inside `_company_news`, immediately after the tier-1 knowledge-base block returns nothing (i.e. after the `if kb_hits:` branch ends, before the wichart feed tier begins), insert:

```python
    # Tier 2: TCBS — a live, first-party, ticker-tagged feed plus the corporate
    # event calendar. Below the knowledge base, whose curated research is the
    # better signal; above the scraped feed and the open web.
    tcbs_block = _best_effort(
        f"tcbs_news[{sym}]", tcbs_tiers.company_news, sym, start_date, end_date
    )
    if tcbs_block:
        logger.info("company_news[%s]: tier 2 TCBS HIT", sym)
        return tcbs_block
```

Renumber the tier comments in that docstring so wichart, report metadata and web search read as tiers 3, 4 and 5.

- [ ] **Step 5: Run the tests**

Run: `cd backend && python -m pytest tests/test_tcbs_tiering.py -v`
Expected: PASS (28 tests).

- [ ] **Step 6: Run the whole suite**

Run: `cd backend && python -m pytest tests/ -q`
Expected: no new failures.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/tradingagents/tcbs_tiers.py \
        backend/app/services/tradingagents/vn_data.py \
        backend/tests/test_tcbs_tiering.py
git commit -m "feat(tcbs): company news and corporate events tier"
```

---

### Task 10: Integration test, lint, and end-to-end verification

**Files:**
- Modify: `backend/tests/test_tcbs_tiering.py`
- Modify: `backend/requirements.lock.txt`

**Interfaces:**
- Consumes: everything above.
- Produces: a marked integration test that skips without a token.

- [ ] **Step 1: Write the integration test**

Append to `backend/tests/test_tcbs_tiering.py`:

```python
def _tcbs_connected() -> bool:
    try:
        from app.services import tcbs_mcp_client

        return tcbs_mcp_client.enabled()
    except Exception:  # noqa: BLE001 — no store, no connection
        return False


requires_tcbs = pytest.mark.skipif(
    not _tcbs_connected(),
    reason="no TCBS token; run: python backend/scripts/tcbs_login.py login",
)


@requires_tcbs
@pytest.mark.integration
def test_live_ticker_overview_answers():
    from app.services import tcbs_mcp_client

    payload = tcbs_mcp_client.call("getTickerOverview", ticker="TCB")
    assert payload
```

- [ ] **Step 2: Register the marker**

Check `backend/pytest.ini` / `pyproject.toml` for a `markers` list. If `integration` is not registered, add it alongside the existing markers so `--strict-markers` does not fail.

- [ ] **Step 3: Run everything**

Run:
```bash
cd backend && python -m pytest tests/test_tcbs_token_store.py tests/test_tcbs_login.py \
  tests/test_tcbs_mcp_client.py tests/test_tcbs_tiering.py -v
cd backend && python -m pytest tests/ -q
```
Expected: all pass; the integration test runs if logged in, skips otherwise.

- [ ] **Step 4: Lint**

Run:
```bash
ruff check backend/app/services/tcbs_mcp_client.py \
           backend/app/services/tcbs_token_store.py \
           backend/app/services/tradingagents/tcbs_tiers.py \
           backend/scripts/tcbs_login.py \
           backend/app/db/models/tcbs.py
```
Expected: no findings.

- [ ] **Step 5: Confirm the submodule is untouched**

Run: `git -C vendor/TradingAgents status --short && git status --short vendor/`
Expected: both empty. **A non-empty result means a Global Constraint was violated** — move the change into `backend/`.

- [ ] **Step 6: Verify the fallback path**

Run:
```bash
cd backend && TCBS_ENABLED=0 python -m pytest tests/ -q
```
Expected: identical results to the run in Step 3. This is the regression guard: with TCBS switched off, nothing behaves differently.

- [ ] **Step 7: End-to-end on a bank and a non-bank**

Run one analysis on `TCB` (bank) and one on `HPG` (non-bank) through the normal
`POST /trading-agents/run` path. Confirm in the reports that: the fundamentals
section carries the peer table and rating; the statements carry the industry
average; the news section carries corporate events; and the insider section is
no longer `INSIDER_DATA_UNAVAILABLE`. Compare against a pre-merge run of the
same two symbols to confirm the section shapes the prompts expect are intact.

- [ ] **Step 8: Refresh the lock file**

Run: `cd backend && pip freeze > requirements.lock.txt`
Expected: `mcp` and its transitive dependencies appear.

- [ ] **Step 9: Commit**

```bash
git add backend/tests/test_tcbs_tiering.py backend/requirements.lock.txt
git commit -m "test(tcbs): live integration check and lock refresh"
```

---

## Self-Review

**Spec coverage:**

| Spec item | Task |
|---|---|
| `tcbs_oauth_tokens` table + migration | 1 |
| `tcbs_login.py` (login/status/logout) | 2 |
| Per-resource OAuth discovery, DCR, PKCE S256 | 2 |
| `tcbs_mcp_client.py`, async→sync bridge | 3 |
| `tools/list` dump committed | 4 |
| Cache TTL, refresh-on-401 | 5 |
| `get_insider_transactions` tier | 6 |
| Bank/non-bank resolver | 7 |
| `get_fundamentals` tier (+ peers, rating) | 7 |
| Statement tiers (+ industry averages, cash-flow analysis) | 8 |
| `get_news` tier below the KB (+ corporate events) | 9 |
| `TCBS_ENABLED` kill switch | 3 (`enabled()`), verified in 10 |
| Fallback stays byte-identical | 6, 7, 8, 9 tests; verified in 10 Step 6 |
| No unit test touches the network | all — the live checks are 4, 5 Step 5, 10 |
| Zero submodule diff | verified in 10 Step 5 |

No spec requirement is unassigned.

**Deviation from the spec worth noting:** the spec listed `vn_data.py` as the
only changed file for the tiers. The rendering lives in a new
`tcbs_tiers.py` instead, with `vn_data.py` keeping only the call sites. That
file is already 1,774 lines; this keeps each tool's diff to three or four
lines. The behaviour is exactly what the spec describes.

**Ordering note:** the spec put schema discovery first. It cannot be — discovery
needs a session, and a session needs the store and the login. So Tasks 1–3 build
the minimum needed to log in, Task 4 does the discovery, and Tasks 5–9 are
written against the dump. Task 4 Step 4 is the reconciliation gate: **the dump
wins over the argument names guessed in this plan.**

**Type consistency:** `TcbsCredentials` fields are used identically in Tasks 1,
2 and 5. `tcbs_tiers` helpers (`_rows`, `_try`, `_first`, `_fmt`, `is_bank`) are
defined in Tasks 6 and 7 before Tasks 8 and 9 consume them. `statement(symbol,
kind, freq)` uses the same `cdkt`/`kqkd`/`lctt` keys as `_STATEMENTS` at
`vn_data.py:464`. `call()` is defined in Task 5 and used from Task 6 onward;
Task 6 is ordered after it.

**Human-in-the-loop:** Task 4 needs a browser and an iOTP device and cannot be
delegated to a subagent. Task 10 Step 7 needs a human reading two reports.
