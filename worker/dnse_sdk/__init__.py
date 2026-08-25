#!/usr/bin/env python3
"""
Vendor root for the official DNSE OpenAPI SDK (``dnse_sdk/dnse``).
Import directly::
    from dnse_sdk.dnse import DNSEClient, TradingClient
"""
from __future__ import annotations


def _ensure_websockets_compat() -> None:
    """Official SDK imports ``websockets.ClientConnection`` (v13+); alias on v12."""
    import websockets

    if hasattr(websockets, "ClientConnection"):
        return
    try:
        from websockets import WebSocketClientProtocol as _ClientConnection
    except ImportError:  # pragma: no cover
        from websockets.legacy.client import WebSocketClientProtocol as _ClientConnection
    websockets.ClientConnection = _ClientConnection  # type: ignore[attr-defined]


_ensure_websockets_compat()

from .dnse import (  # noqa: E402
    DNSEClient,
    TradingClient,
    TradingWebSocketError,
    ConnectionError,
    ConnectionClosed,
    AuthenticationError,
    SubscriptionError,
    EncodingError,
)
from .dnse.api._version import __version__ as APIVersion
from .dnse.websocket._version import __version__ as WSVersion

__all__ = [
    "DNSEClient",
    "TradingClient",
    "APIVersion",
    "WSVersion",
    "TradingWebSocketError",
    "ConnectionError",
    "ConnectionClosed",
    "AuthenticationError",
    "SubscriptionError",
    "EncodingError",
]
