"""DNSE OpenAPI market-data WebSocket source for Bytewax (Trade Extra).

Implements the DNSE OpenAPI realtime protocol documented at
developers.dnse.com.vn (SDK ``python/dnse/websocket`` +
``websocket-marketdata/trade_extra.py``). Unlike the legacy datafeed, this is a
**raw secure WebSocket** (not MQTT) at ``wss://ws-openapi.dnse.com.vn/v1/stream``
with HMAC-SHA256 authentication and JSON frames:

  1. connect to ``{base_url}/v1/stream?encoding=json`` and read the welcome frame
  2. send an ``auth`` frame signed HMAC-SHA256 over ``{api_key}:{ts}:{nonce}``
  3. subscribe to the Trade-Extra channel per board: ``tick_extra.{board}.json``
     with the watchlist ``symbols``
  4. consume frames; a matched trade carries ``T == "te"`` and the fields
     ``symbol``, ``matchPrice``, ``matchQtty``, ``side``, ``time``
  5. answer server ``ping`` frames with ``pong``

The gateway SDK is fully async; Bytewax pulls synchronously via ``next_batch``.
The partition therefore runs the asyncio client in a background thread and hands
messages to the runtime through a thread-safe queue. Each matched trade is
reshaped into the API-style payload that ``core.tick_contract.normalize_tick``
already parses and emitted as ``(topic, payload_bytes)`` — the exact contract the
MQTT tick source used, so ``workers.block_episode_ingest`` needs no change.

Credentials come from ``DNSE_API_KEY`` / ``DNSE_API_SECRET`` and are never logged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import queue
import ssl
import threading
import time
from datetime import datetime, timezone
from typing import List, Optional

import certifi
import orjson
import websockets
from bytewax.inputs import DynamicSource, StatelessSourcePartition
from dotenv import load_dotenv

from core.tick_contract import SIDE_BUY, SIDE_SELL, SIDE_UNKNOWN

load_dotenv()
log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "wss://ws-openapi.dnse.com.vn"
# Continuous-trading + odd/negotiated boards DNSE exposes for KRX equities.
DEFAULT_BOARDS = ["G1", "G3", "G4", "G7", "T1", "T2", "T3", "T4", "T6"]
TRADE_EXTRA_MSG_TYPE = "te"

# DNSE Trade-Extra reports the *aggressor* side as "BUY"/"SELL" (chiều mua/bán
# chủ động). Map it to this repo's canonical side ints so normalize_tick
# (which only knows "B"/"S"/1/2) resolves it correctly instead of UNKNOWN.
_SIDE_MAP = {
    "BUY": SIDE_BUY, "B": SIDE_BUY, "BID": SIDE_BUY, "1": SIDE_BUY, 1: SIDE_BUY,
    "SELL": SIDE_SELL, "S": SIDE_SELL, "ASK": SIDE_SELL, "2": SIDE_SELL, 2: SIDE_SELL,
}


def normalize_side(value) -> int:
    """Map a DNSE aggressor side ("BUY"/"SELL", etc.) to 1/2/0."""
    if isinstance(value, str):
        return _SIDE_MAP.get(value.strip().upper(), SIDE_UNKNOWN)
    return _SIDE_MAP.get(value, SIDE_UNKNOWN)


# ---------------------------------------------------------------------------
# Protocol helpers (pure — unit-testable without any network)
# ---------------------------------------------------------------------------
def compute_signature(api_key: str, api_secret: str, timestamp: int, nonce: str) -> str:
    """HMAC-SHA256 hex over ``{api_key}:{timestamp}:{nonce}`` (DNSE AuthManager)."""
    message = f"{api_key}:{timestamp}:{nonce}"
    return hmac.new(
        api_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def build_auth_message(api_key: str, api_secret: str, now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    timestamp = int(now)
    nonce = str(int(now * 1_000_000))
    return {
        "action": "auth",
        "api_key": api_key,
        "signature": compute_signature(api_key, api_secret, timestamp, nonce),
        "timestamp": timestamp,
        "nonce": nonce,
    }


def trade_extra_channel(board: str, encoding: str = "json") -> str:
    """Trade-Extra channel name for a board, e.g. ``tick_extra.G1.json``."""
    return f"tick_extra.{board}.{'msgpack' if encoding == 'msgpack' else 'json'}"


def build_subscribe_message(channel: str, symbols: List[str]) -> dict:
    return {"action": "subscribe", "channels": [{"name": channel, "symbols": symbols}]}


def _time_to_iso(value) -> Optional[str]:
    """DNSE ``time`` (ISO str, protobuf {Seconds,Nanos}, or unix s/ms) -> UTC ISO."""
    if value is None:
        return None
    if isinstance(value, str):
        return value  # already ISO; normalize_tick handles the 'Z' suffix
    if isinstance(value, dict):
        seconds = value.get("Seconds", value.get("seconds", 0))
        nanos = value.get("Nanos", value.get("nanos", 0))
        return datetime.fromtimestamp(seconds + nanos / 1e9, tz=timezone.utc).isoformat()
    if isinstance(value, (int, float)):
        secs = value / 1000.0 if value > 1e12 else float(value)
        return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat()
    return None


def trade_extra_to_tick_payload(data: dict) -> Optional[dict]:
    """Reshape a decoded Trade-Extra frame into a ``normalize_tick`` API payload.

    Returns None when required fields are missing. DNSE's ``"BUY"``/``"SELL"``
    aggressor side and its protobuf ``time`` ({Seconds, Nanos}, UTC) are mapped
    to the canonical int side and a UTC ISO timestamp here; ``normalize_tick``
    then produces the final canonical tick.
    """
    symbol = data.get("symbol")
    sending_time = _time_to_iso(data.get("time"))
    if not symbol or sending_time is None or data.get("matchPrice") is None:
        return None
    return {
        "symbol": symbol,
        "sendingTime": sending_time,
        "matchPrice": data.get("matchPrice"),
        "matchQtty": data.get("matchQtty"),
        "side": normalize_side(data.get("side")),
    }


# ---------------------------------------------------------------------------
# Bytewax source partition (async client on a background thread)
# ---------------------------------------------------------------------------
class DnseTradePartition(StatelessSourcePartition):
    """One WebSocket connection consuming Trade-Extra for the watchlist.

    Set ``start=False`` to build the partition without opening the socket (used
    by the offline tests, which drive ``_process_frame`` / ``next_batch``
    directly).
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbols: List[str],
        boards: Optional[List[str]] = None,
        base_url: str = DEFAULT_BASE_URL,
        encoding: str = "json",
        batch_size: int = 1024,
        connect_timeout: float = 60.0,
        start: bool = True,
    ):
        if not api_key or not api_secret:
            raise RuntimeError(
                "DNSE OpenAPI credentials missing: set DNSE_API_KEY / DNSE_API_SECRET"
            )
        self._api_key = api_key
        self._api_secret = api_secret
        self._symbols = list(symbols)
        self._boards = boards or DEFAULT_BOARDS
        self._base_url = base_url
        self._encoding = encoding
        self._batch_size = batch_size
        self._connect_timeout = connect_timeout

        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        if start:
            self._thread = threading.Thread(
                target=self._run, name="dnse-ws", daemon=True
            )
            self._thread.start()

    # -- decode-side (pure) --------------------------------------------------
    def _process_frame(self, data: dict) -> Optional[str]:
        """Handle one decoded frame.

        Enqueues matched trades; returns a reply string to send back (``pong``)
        or None. Kept side-effect-light so tests can call it directly.
        """
        action = data.get("action") or data.get("a")
        if action == "ping":
            return json.dumps({"action": "pong"})
        if data.get("T") == TRADE_EXTRA_MSG_TYPE:
            payload = trade_extra_to_tick_payload(data)
            if payload is not None:
                topic = f"tick_extra/{payload['symbol']}"
                self._q.put((topic, orjson.dumps(payload)))
        return None

    # -- async client (background thread) ------------------------------------
    def _run(self) -> None:
        import asyncio

        while not self._stop.is_set():
            try:
                asyncio.run(self._consume())
            except Exception as exc:  # noqa: BLE001 - keep the thread alive
                log.warning("DNSE WS session ended (%s); reconnecting in 5s", exc)
                self._stop.wait(5.0)

    async def _consume(self) -> None:
        url = f"{self._base_url}/v1/stream?encoding={self._encoding}"
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        log.info("Connecting to DNSE OpenAPI %s — %d symbols, %d boards",
                 url, len(self._symbols), len(self._boards))
        async with websockets.connect(
            url, ssl=ssl_ctx, ping_interval=30, ping_timeout=30, max_queue=512
        ) as ws:
            await ws.recv()  # welcome frame
            await ws.send(json.dumps(build_auth_message(self._api_key, self._api_secret)))
            resp = json.loads(await ws.recv())
            if (resp.get("action") or resp.get("a")) != "auth_success":
                raise RuntimeError(f"DNSE OpenAPI auth failed: {resp}")

            for board in self._boards:
                channel = trade_extra_channel(board, self._encoding)
                await ws.send(json.dumps(build_subscribe_message(channel, self._symbols)))
            log.info("Subscribed Trade-Extra on %d boards", len(self._boards))

            async for raw in ws:
                if self._stop.is_set():
                    break
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                reply = self._process_frame(data)
                if reply is not None:
                    await ws.send(reply)

    # -- Bytewax surface -----------------------------------------------------
    def next_batch(self, _sched=None):
        batch = []
        try:
            while len(batch) < self._batch_size:
                batch.append(self._q.get_nowait())
        except queue.Empty:
            pass
        return batch

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Bytewax dynamic source
# ---------------------------------------------------------------------------
class DnseTradeSource(DynamicSource):
    """Bytewax source over DNSE OpenAPI's Trade-Extra WebSocket feed.

    Emits ``(topic, payload_bytes)`` per matched trade — a payload shaped for
    ``core.tick_contract.normalize_tick``. One partition per worker.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbols: List[str],
        boards: Optional[List[str]] = None,
        base_url: str = DEFAULT_BASE_URL,
        encoding: str = "json",
        batch_size: int = 1024,
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._symbols = symbols
        self._boards = boards
        self._base_url = base_url
        self._encoding = encoding
        self._batch_size = batch_size

    def build(self, _step_id: str, _worker_index: int, _worker_count: int):
        return DnseTradePartition(
            self._api_key,
            self._api_secret,
            self._symbols,
            boards=self._boards,
            base_url=self._base_url,
            encoding=self._encoding,
            batch_size=self._batch_size,
        )
