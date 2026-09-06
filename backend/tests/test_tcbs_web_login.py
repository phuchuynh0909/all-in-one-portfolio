"""The in-app TCBS login: status, authorize, and the unauthenticated callback."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.v1.routes import trading_agents as routes
from app.main import app
from app.services.tcbs_token_store import TcbsCredentials

STATUS = "/api/v1/trading-agents/tcbs/status"
AUTHORIZE = "/api/v1/trading-agents/tcbs/authorize"
CALLBACK = "/api/v1/trading-agents/tcbs/callback"

_META = {
    "authorization_endpoint": "https://mcp.tcbs.com.vn/tcinvest/authorize",
    "token_endpoint": "https://mcp.tcbs.com.vn/tcinvest/token",
    "registration_endpoint": "https://mcp.tcbs.com.vn/tcinvest/register",
}


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean_pending():
    routes._PENDING.clear()
    yield
    routes._PENDING.clear()


@pytest.fixture
def no_token(monkeypatch):
    monkeypatch.setattr(routes, "load_credentials", lambda: None)


@pytest.fixture
def flow(monkeypatch):
    """Stub the whole OAuth handshake; record what got stored."""
    stored = {}
    monkeypatch.setattr(routes.tcbs_oauth, "discover_auth_server", lambda *a, **k: _META)
    monkeypatch.setattr(
        routes.tcbs_oauth, "register_client", lambda *a, **k: ("cid-1", "csec-1")
    )
    monkeypatch.setattr(
        routes.tcbs_oauth, "exchange_code", lambda *a, **k: {"access_token": "tok-1"}
    )
    monkeypatch.setattr(
        routes.tcbs_oauth,
        "store_tokens",
        lambda cid, csec, payload: stored.update(
            client_id=cid, client_secret=csec, payload=payload
        ),
    )
    return stored


def test_status_reports_not_connected(client, no_token):
    body = client.get(STATUS).json()
    assert body == {"connected": False, "expired": False, "expires_at": None}


def test_status_reports_connected_and_live(client, monkeypatch):
    monkeypatch.setattr(
        routes,
        "load_credentials",
        lambda: TcbsCredentials(
            client_id="cid",
            client_secret=None,
            access_token="tok",
            refresh_token="ref",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
        ),
    )
    body = client.get(STATUS).json()
    assert body["connected"] is True
    assert body["expired"] is False
    assert body["expires_at"]


def test_status_reports_an_expired_token(client, monkeypatch):
    monkeypatch.setattr(
        routes,
        "load_credentials",
        lambda: TcbsCredentials(
            client_id="cid",
            client_secret=None,
            access_token="tok",
            refresh_token=None,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        ),
    )
    body = client.get(STATUS).json()
    assert body["connected"] is True
    assert body["expired"] is True


def test_authorize_returns_a_url_and_remembers_the_flow(client, flow):
    body = client.get(AUTHORIZE).json()

    assert body["authorization_url"].startswith(_META["authorization_endpoint"])
    assert "code_challenge_method=S256" in body["authorization_url"]
    # Exactly one pending flow, keyed by the state echoed in the URL.
    assert len(routes._PENDING) == 1
    state = next(iter(routes._PENDING))
    assert f"state={state}" in body["authorization_url"]


def test_authorize_registers_the_same_redirect_it_advertises(client, flow, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        routes.tcbs_oauth,
        "register_client",
        lambda endpoint, redirect_uri: seen.update(redirect=redirect_uri)
        or ("cid-1", "csec-1"),
    )
    body = client.get(AUTHORIZE).json()
    # Whatever redirect_uri we advertise, registration must use the same one:
    # TCBS validates the pair, and a mismatch fails at the exchange.
    assert seen["redirect"] == body["redirect_uri"]
    assert seen["redirect"].startswith("http://127.0.0.1:")


def test_callback_stores_the_tokens_and_returns_to_the_app(client, flow):
    state = _start(client, return_to="http://localhost:5173/trading-agents")

    resp = client.get(
        CALLBACK, params={"code": "abc", "state": state}, follow_redirects=False
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "http://localhost:5173/trading-agents"
    assert flow["payload"]["access_token"] == "tok-1"
    assert flow["client_id"] == "cid-1"
    # The flow is single-use: a replayed code must not find it again.
    assert routes._PENDING == {}


def test_callback_shows_a_page_when_there_is_nowhere_safe_to_return(client, flow):
    state = _start(client)
    resp = client.get(CALLBACK, params={"code": "abc", "state": state})
    assert resp.status_code == 200
    assert "TCBS connected" in resp.text
    assert flow["payload"]["access_token"] == "tok-1"


def test_callback_refuses_to_return_to_a_foreign_origin(client, flow):
    # An unchecked return_to would make the callback an open redirect.
    state = _start(client, return_to="https://evil.example.com/steal")
    resp = client.get(
        CALLBACK, params={"code": "abc", "state": state}, follow_redirects=False
    )
    assert resp.status_code == 200
    assert "evil.example.com" not in resp.text


def test_a_replayed_code_finds_nothing(client, flow):
    state = _start(client)
    client.get(CALLBACK, params={"code": "abc", "state": state})
    again = client.get(CALLBACK, params={"code": "abc", "state": state})
    assert again.status_code == 400


def test_an_abandoned_login_expires(client, flow, monkeypatch):
    state = _start(client)
    # Jump past the window rather than sleeping through it.
    routes._PENDING[state]["started"] -= routes._PENDING_TTL_SECONDS + 1
    resp = client.get(CALLBACK, params={"code": "abc", "state": state})
    assert resp.status_code == 400
    assert "expired" in resp.json()["detail"].lower()


def test_callback_rejects_an_unknown_state(client, flow):
    resp = client.get(CALLBACK, params={"code": "abc", "state": "not-a-real-state"})
    assert resp.status_code == 400
    assert "state" in resp.json()["detail"].lower()
    assert flow == {}  # nothing stored


def test_callback_surfaces_a_provider_error(client, flow):
    state = _start(client)
    resp = client.get(CALLBACK, params={"error": "access_denied", "state": state})
    assert resp.status_code == 400
    assert "access_denied" in resp.json()["detail"]
    assert flow == {}


def test_callback_without_a_code_is_rejected(client, flow):
    state = _start(client)
    resp = client.get(CALLBACK, params={"state": state})
    assert resp.status_code == 400
    assert flow == {}


@pytest.mark.real_auth
def test_callback_is_reachable_without_a_token(client):
    # TCBS redirects the browser here with no Authorization header, so the route
    # must be exempt. It still refuses the request on the state check.
    resp = client.get(CALLBACK, params={"code": "abc", "state": "nope"})
    assert resp.status_code != 401


@pytest.mark.real_auth
def test_authorize_still_requires_a_token(client):
    # Only the callback is exempt: the frontend fetches this one over XHR and
    # can carry the header, so it stays behind the guard.
    assert client.get(AUTHORIZE).status_code == 401


@pytest.mark.real_auth
def test_status_still_requires_a_token(client):
    assert client.get(STATUS).status_code == 401


def _start(client, return_to: str | None = None) -> str:
    """Run authorize and return the state it minted."""
    client.get(AUTHORIZE, params={"return_to": return_to} if return_to else None)
    return next(iter(routes._PENDING))


# --- the paste-the-code flow ------------------------------------------------
#
# TCBS's authorization server refuses any non-loopback redirect_uri (verified
# against the live server: a hosted https callback gets 400 "does not match the
# proxy origin", while http://127.0.0.1:<port>/callback is accepted even with
# nothing listening). So the browser is sent to a loopback address that will
# fail to load, and the user pastes the resulting URL back into the app.

COMPLETE = "/api/v1/trading-agents/tcbs/complete"


def test_authorize_uses_a_loopback_redirect_uri(client, flow):
    """The whole point: a hosted redirect_uri is rejected by TCBS."""
    body = client.get(AUTHORIZE).json()
    assert body["redirect_uri"].startswith("http://127.0.0.1:")
    assert body["redirect_uri"].endswith("/callback")
    state = next(iter(routes._PENDING))
    assert routes._PENDING[state]["redirect_uri"] == body["redirect_uri"]


def test_authorize_ignores_a_hosted_redirect_override(client, flow, monkeypatch):
    """TCBS rejects hosted callbacks, including a stale deployment override."""
    monkeypatch.setenv("TCBS_REDIRECT_BASE", "https://api.example.com")

    body = client.get(AUTHORIZE).json()

    assert body["redirect_uri"].startswith("http://127.0.0.1:")
    assert body["redirect_uri"].endswith("/callback")

def test_complete_accepts_the_whole_pasted_url(client, flow):
    state = _start(client)
    pasted = f"http://127.0.0.1:8765/callback?code=abc123&state={state}"

    res = client.post(COMPLETE, json={"pasted": pasted})

    assert res.status_code == 200, res.text
    assert res.json()["connected"] is True
    assert flow["client_id"] == "cid-1"
    assert flow["payload"] == {"access_token": "tok-1"}


def test_complete_accepts_a_bare_query_fragment(client, flow):
    """People copy inconsistently; a bare query string must work too."""
    state = _start(client)

    res = client.post(COMPLETE, json={"pasted": f"?code=abc123&state={state}"})

    assert res.status_code == 200, res.text
    assert flow["payload"] == {"access_token": "tok-1"}


def test_complete_is_single_use(client, flow):
    """A pasted URL is a credential; replaying it must miss."""
    state = _start(client)
    pasted = f"http://127.0.0.1:8765/callback?code=abc123&state={state}"

    assert client.post(COMPLETE, json={"pasted": pasted}).status_code == 200
    replay = client.post(COMPLETE, json={"pasted": pasted})
    assert replay.status_code == 400


def test_complete_rejects_an_unknown_state(client, flow):
    res = client.post(
        COMPLETE,
        json={"pasted": "http://127.0.0.1:8765/callback?code=abc&state=never-minted"},
    )
    assert res.status_code == 400
    assert "expired" in res.json()["detail"].lower()


def test_complete_surfaces_an_error_from_the_pasted_url(client, flow):
    """TCBS reports refusal in the query string, not by failing to redirect."""
    state = _start(client)
    pasted = (
        f"http://127.0.0.1:8765/callback?error=access_denied"
        f"&error_description=User+said+no&state={state}"
    )

    res = client.post(COMPLETE, json={"pasted": pasted})

    assert res.status_code == 400
    assert "access_denied" in res.json()["detail"]


def test_complete_rejects_a_url_with_no_code(client, flow):
    state = _start(client)
    res = client.post(COMPLETE, json={"pasted": f"http://127.0.0.1:8765/callback?state={state}"})
    assert res.status_code == 400


def test_complete_rejects_unparseable_junk(client, flow):
    _start(client)
    res = client.post(COMPLETE, json={"pasted": "i forgot to copy anything"})
    assert res.status_code == 400


def test_complete_requires_authentication(flow):
    """Unlike the GET callback, this one is called from inside the app."""
    from app.api.deps import require_user

    app.dependency_overrides.pop(require_user, None)  # undo the autouse stub
    try:
        with TestClient(app) as anon:
            res = anon.post(COMPLETE, json={"pasted": "?code=a&state=b"})
        assert res.status_code == 401
    finally:
        pass  # the autouse fixture reinstates the override for the next test
