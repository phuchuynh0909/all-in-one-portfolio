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
    # RFC 7636 appendix B: this verifier must produce this challenge. Patched on
    # the module that owns the primitive -- the CLI only re-exports it.
    from app.services import tcbs_oauth

    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    monkeypatch.setattr(tcbs_oauth, "new_verifier", lambda: verifier)
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
