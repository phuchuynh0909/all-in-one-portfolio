"""The TCBS OAuth 2.0 flow, shared by the login CLI and the web login routes.

Both entry points run the same handshake -- discover, register, PKCE, exchange,
refresh -- and differ only in how they get the user to the authorization page
and how they catch the redirect: the CLI opens a browser and listens on a
loopback port, the API redirects the caller's browser and catches the callback
on a route. Keeping the protocol in one module is what stops the two from
drifting into disagreement about which authorization server is authoritative.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

MCP_URL = os.getenv("TCBS_MCP_URL", "https://mcp.tcbs.com.vn/mcp/tcinvest/")
HTTP_TIMEOUT = float(os.getenv("TCBS_TIMEOUT", "30"))

CLIENT_NAME = "all-in-one-portfolio TradingAgents"


class TcbsOAuthError(RuntimeError):
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


def discover_auth_server(resource_url: str | None = None) -> dict:
    """Follow the protected-resource document to its authorization server."""
    resource_url = (resource_url or MCP_URL).rstrip("/")
    resource_doc = requests.get(
        wellknown_url(resource_url, "oauth-protected-resource"),
        timeout=HTTP_TIMEOUT,
        headers={"Accept": "application/json"},
    )
    resource_doc.raise_for_status()
    servers = resource_doc.json().get("authorization_servers") or []
    if not servers:
        raise TcbsOAuthError(f"{resource_url} names no authorization server")

    as_doc = requests.get(
        wellknown_url(servers[0], "oauth-authorization-server"),
        timeout=HTTP_TIMEOUT,
        headers={"Accept": "application/json"},
    )
    as_doc.raise_for_status()
    return as_doc.json()


def new_verifier() -> str:
    """A fresh PKCE code verifier. Seam for the RFC test vector."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def pkce_pair() -> tuple[str, str]:
    """``(verifier, challenge)`` for PKCE S256."""
    verifier = new_verifier()
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def register_client(registration_endpoint: str, redirect_uri: str) -> tuple[str, str | None]:
    """Dynamic client registration. Returns ``(client_id, client_secret)``."""
    resp = requests.post(
        registration_endpoint,
        json={
            "client_name": CLIENT_NAME,
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
        raise TcbsOAuthError(f"registration returned no client_id: {payload}")
    return client_id, payload.get("client_secret")


def authorization_url(
    meta: dict, *, client_id: str, redirect_uri: str, state: str, challenge: str
) -> str:
    """Where to send the user to approve access."""
    return meta["authorization_endpoint"] + "?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )


def _post_token(meta: dict, data: dict) -> dict:
    resp = requests.post(meta["token_endpoint"], data=data, timeout=HTTP_TIMEOUT)
    if resp.status_code >= 400:
        raise TcbsOAuthError(
            f"token endpoint returned {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


def exchange_code(
    meta: dict,
    *,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str | None,
    verifier: str,
) -> dict:
    """Trade the authorization code for tokens."""
    return _post_token(
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


def refresh_access_token(
    meta: dict, *, refresh_token: str, client_id: str, client_secret: str | None
) -> dict:
    """Trade the refresh token for a new access token."""
    return _post_token(
        meta,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret or "",
        },
    )


def expiry_from(payload: dict) -> datetime | None:
    """Absolute expiry from the token response's ``expires_in``, if it gave one."""
    expires_in = payload.get("expires_in")
    if not expires_in:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))


def store_tokens(client_id: str, client_secret: str | None, payload: dict) -> None:
    """Persist a token response as the install's credentials."""
    from app.services.tcbs_token_store import TcbsCredentials, save

    save(
        TcbsCredentials(
            client_id=client_id,
            client_secret=client_secret,
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_at=expiry_from(payload),
        )
    )


def parse_callback(path_or_query: str, expected_state: str) -> str:
    """The authorization code from a redirect, or raise.

    Accepts a full path with a query string or a bare query string, so the CLI's
    loopback handler and the API route can both hand it whatever they received.
    """
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(path_or_query).query) or (
        urllib.parse.parse_qs(path_or_query)
    )
    if "error" in query:
        raise TcbsOAuthError(
            f"authorization failed: {query['error'][0]} "
            f"({query.get('error_description', [''])[0]})"
        )
    if query.get("state", [None])[0] != expected_state:
        raise TcbsOAuthError("authorization state did not match; aborting")
    code = query.get("code", [None])[0]
    if not code:
        raise TcbsOAuthError("callback carried no authorization code")
    return code


def describe(creds: Any | None) -> dict:
    """The connection state the UI renders: connected, expired, until when."""
    if creds is None:
        return {"connected": False, "expired": False, "expires_at": None}
    return {
        "connected": True,
        "expired": creds.is_expired(),
        "expires_at": creds.expires_at.isoformat() if creds.expires_at else None,
    }
