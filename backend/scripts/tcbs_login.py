#!/usr/bin/env python3
"""Connect this install to the TCBS MCP server (OAuth 2.0 + iOTP).

Usage:
    python backend/scripts/tcbs_login.py login
    python backend/scripts/tcbs_login.py status
    python backend/scripts/tcbs_login.py logout
    python backend/scripts/tcbs_login.py tools [--dump docs/tcbs-mcp-tools.json]

Talks to MySQL directly, like ``manage_users.py``, so it works whether or not
the stack is running -- and so the token it writes is readable by the backend
container, which is where it is actually spent.

``login`` opens a browser: TCBS requires an account login plus an iOTP
confirmation, which is why this step cannot be automated. It is one-time --
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

    def do_GET(self):  # noqa: N802 -- BaseHTTPRequestHandler's name
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
            "Warning: no refresh token was issued -- you will have to log in "
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
