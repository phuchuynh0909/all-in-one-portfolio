"""Client for the TCBS MCP server (https://mcp.tcbs.com.vn/mcp/tcinvest/).

Read-only, ticker-scoped broker data: company overviews, ratio sets split for
banks and non-banks, statements with industry averages, insider dealing,
foreign flow, corporate events and ratings, for HOSE, HNX and UPCOM.

Two shapes worth knowing about:

  * **Async SDK, sync callers.** Everything downstream -- ``route_to_vendor``
    and every tool in ``vn_data.py`` -- is synchronous. One event loop runs in a
    daemon thread for the process lifetime and coroutines are submitted to it.
    Not ``asyncio.run`` per call: that would tear down the MCP session, and the
    OAuth handshake behind it, on every tool invocation.
  * **The session is long-lived and repairable.** ``reset()`` drops it; the next
    call rebuilds. That is how a 401 refresh recovers.

The bearer token rides on a pre-configured ``httpx2.AsyncClient``: the MCP SDK's
2.x transport takes no ``headers`` argument of its own.

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
    except Exception as exc:  # noqa: BLE001 -- a store failure means "no tier"
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

    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    creds = _load_credentials()
    if creds is None:
        raise TcbsUnavailable(
            "no TCBS token stored; run: python backend/scripts/tcbs_login.py login"
        )

    stack = AsyncExitStack()
    try:
        # The 2.x transport takes no headers of its own, so authorization is
        # carried by the HTTP client handed to it.
        http_client = await stack.enter_async_context(
            httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {creds.access_token}"},
                timeout=TIMEOUT,
            )
        )
        read, write = await stack.enter_async_context(
            streamable_http_client(MCP_URL, http_client=http_client)
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
    except Exception as exc:  # noqa: BLE001 -- a torn-down session is the goal
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
