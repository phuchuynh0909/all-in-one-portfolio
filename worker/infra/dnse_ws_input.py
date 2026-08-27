"""DNSE OpenAPI market-data WebSocket source for Bytewax (Trade Extra).

Transport, authentication, encoding and heartbeat are delegated to the official
DNSE SDK vendored at ``worker/dnse_sdk`` (``dnse.websocket.TradingClient``) —
the connect/TLS/welcome handshake, HMAC-SHA256 auth, JSON *and* MessagePack
framing, the heartbeat, and the receive/dispatch task fan-out. This module
supplies only what that SDK does not cover for this feed, and adapts the result
to Bytewax.

Two upstream defects are repaired in the vendored SDK itself rather than worked
around here, because both governed every channel: ``parse_timestamp`` dropped
the UTC offset, and market data was routed solely on ``data["T"]`` — which
Trade-Extra does not carry, so the channel delivered nothing while every health
signal passed. Both are marked ``LOCAL PATCH`` in the source; re-vendoring the
SDK reverts them, and the tests under "Vendored-SDK patch" in
``tests/test_dnse_ws_input.py`` are what will say so.

What this module owns, and why
------------------------------
**Reconnect pacing.** The SDK's own reconnect loop is disabled
(``auto_reconnect=False``, ``max_retries=1``) so ``_run`` is the only backoff in
play; the SDK would otherwise retry a failed connect ten times on its own
schedule inside a single attempt of ours.

**One subscribe frame per board**, through the SDK's ``subscribe_trade_extra``
and driven from the configured board list (its ``board_id=None`` default means
*its* nine boards, not ours). Batching every board into one frame relies on
``channels`` accepting a list, which no upstream path exercises — and a gateway
that read only ``channels[0]`` would look exactly like quiet boards.

**Raw dicts, not the SDK's ``TradeExtra`` model.** The partition reshapes each
frame into the API-style payload ``core.tick_contract.normalize_tick`` already
parses and emits ``(topic, payload_bytes)`` — the exact contract the MQTT tick
source used, so ``workers.tick_ingest`` and ``workers.block_episode_ingest``
need no per-source handling. The model would rename those fields
(``matchPrice`` -> ``price``) only to have them undone.

**A decoder and a control-frame log** that make silence diagnosable: an
undecodable frame is counted rather than fatal, and an unrecognised control
frame is surfaced once per shape. A partly-rejected subscription used to look
identical to a market with no trades.

Boards ``G1`` (even lot) and ``G4`` (odd lot) carry both stocks and derivatives,
so a single subscription covers equities and the VN30F futures contract.

The endpoint is only served during the exchange session — out of hours
``ws-openapi.dnse.com.vn`` stops resolving, so a connect fails in
``getaddrinfo`` rather than anything protocol-shaped. Nothing here gates on the
clock: that failure lands in the ordinary backoff, which
``_is_endpoint_unreachable`` keeps at INFO. DNSE also drops every live session
on the wall-clock half hour, so a reconnect twice an hour is normal.

The SDK is fully async; Bytewax pulls synchronously via ``next_batch``. The
partition therefore runs the asyncio client in a background thread and hands
messages to the runtime through a thread-safe queue.

Credentials come from ``DNSE_API_KEY`` / ``DNSE_API_SECRET`` and are never logged.
"""

from __future__ import annotations

import json
import logging
import queue
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import orjson
from bytewax.inputs import DynamicSource, StatelessSourcePartition
from dotenv import load_dotenv

from core.tick_contract import SIDE_BUY, SIDE_SELL, SIDE_UNKNOWN

load_dotenv()
log = logging.getLogger(__name__)

import dnse_sdk  # noqa: F401  (imported for that side effect)
from dnse_sdk.dnse.websocket.client import (
    DEFAULT_BOARDS as SDK_DEFAULT_BOARDS,
    TradingClient,
)
from dnse_sdk.dnse.websocket.encoding import MessageDecoder
from dnse_sdk.dnse.websocket.models import parse_timestamp


def _quiet_sdk_logging() -> None:
    """Stop the SDK from printing to stderr behind the app's logging config.

    ``dnse.websocket.client`` and ``.connection`` each attach a StreamHandler at
    import time and leave ``propagate`` on, so every SDK line would appear twice
    — once formatted by the SDK, once by whatever the worker configured. Drop
    the SDK's handlers and let the records propagate to the root logger like
    every other module's.
    """
    for name in ("dnse_sdk.dnse.websocket.client", "dnse_sdk.dnse.websocket.connection"):
        sdk_log = logging.getLogger(name)
        for handler in list(sdk_log.handlers):
            sdk_log.removeHandler(handler)
        sdk_log.setLevel(logging.NOTSET)


_quiet_sdk_logging()


DEFAULT_BASE_URL = "wss://ws-openapi.dnse.com.vn"

# Every board DNSE exposes for KRX equities: G1 main continuous, G4/G7 odd lot,
# T1..T6 put-through. Taken from the SDK so the valid set cannot drift from it.
# This is the menu for ``DNSE_TRADE_BOARDS``, not what is subscribed.
ALL_BOARDS = list(SDK_DEFAULT_BOARDS)

# What is actually subscribed by default: G1 alone. ``TICK_ALLOWED_BOARDS``
# defaults to G1 too, so every other board was being received and then dropped
# before insert — a wider subscription only bought the per-board tally below.
# G1 carries derivatives as well as equities, so the VN30F contract still
# arrives. ``config.DEFAULT_TRADE_BOARDS`` must agree with this.
DEFAULT_BOARDS = ["G1"]

TRADE_EXTRA_MSG_TYPE = "te"

SUPPORTED_ENCODINGS = ("json", "msgpack")

# Reconnect backoff. Starts where the old fixed delay was and doubles up to the
# cap, so a mid-session blip still recovers in seconds while an endpoint that is
# simply not there — out of hours, or down — is retried once a minute instead of
# twelve times.
_BACKOFF_START = 5.0
_BACKOFF_CAP = 60.0

# A connection that stayed up this long is evidence the endpoint is healthy, so
# the next hiccup starts from the short delay again instead of inheriting a
# backoff grown during an earlier outage.
_HEALTHY_SESSION_SECS = 60.0

# How often the per-board trade split is logged. Frequent enough to answer
# "which boards are live" within a minute or two of a busy open, rare enough not
# to be a per-trade log line.
_BOARD_REPORT_EVERY = 1000

# Control frames are logged whole, truncated at this length: an ack is short,
# but a rejection listing every channel is not.
_CONTROL_FRAME_CHARS = 600

# How often ``_consume`` checks whether close() was called. The SDK owns the
# receive loop, so this is the only place the partition can notice a shutdown
# request; a quarter-second keeps close() well inside its 2s join timeout.
_SHUTDOWN_POLL_SECS = 0.25

# Synthetic action stood in for a frame the codec could not read, so one bad
# frame is counted and skipped instead of tearing down the connection.
_UNDECODABLE_ACTION = "_undecodable"

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
def _is_endpoint_unreachable(exc: BaseException) -> bool:
    """True when the failure is "the endpoint isn't there", not a protocol error.

    Out of hours the feed's host stops resolving, which surfaces as
    ``socket.gaierror: [Errno -2] Name or service not known`` from
    ``getaddrinfo`` — nothing to do with the auth or subscribe framing, and not
    worth a warning. Both ``websockets`` and the SDK's ``WebSocketConnection``
    wrap connect failures in their own exception types (the SDK's
    ``ConnectionError`` is its own class, not the builtin), so the
    ``__cause__``/``__context__`` chain is walked rather than just the outermost
    type.
    """
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, (socket.gaierror, ConnectionError, TimeoutError)):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _time_to_iso(value) -> Optional[str]:
    """DNSE ``time`` (ISO str, protobuf {Seconds,Nanos}, or unix s/ms) -> UTC ISO.

    The conversion itself is the SDK's ``models.parse_timestamp``, patched there
    to return an explicit UTC offset for every input shape rather than the naive
    local string it used to build (see ``dnse_sdk/__init__.py``). Every value
    returned here therefore carries an offset, which is the whole contract: the
    instant a tick happened is not recoverable once it has been written to
    ``ticks`` under the wrong zone, and nothing downstream can tell a wrong
    timestamp from a right one.

    This wrapper adds only what a library cannot: a *visible* rejection. A tick
    dropped in silence at several thousand a second is precisely the failure
    mode that is hardest to notice.
    """
    if value is None:
        return None
    iso = parse_timestamp(value)
    if iso is None:
        _warn_rejected_time(value)
    return iso


# Rejected-timestamp tallies, keyed by reason. If the feed ever does change
# shape, every tick in the session hits the same branch — so reporting widens
# (like the undecodable-frame counter) instead of emitting one warning per trade
# at several thousand a second.
_TIME_REJECTS: Dict[str, int] = {}


def _warn_rejected_time(value) -> None:
    """Report a time ``parse_timestamp`` refused, throttled per reason.

    A naive string is counted separately because it is the one that looks fine:
    it parses, it just does not say *when*, and ``normalize_tick`` would read the
    missing offset as UTC and land an ICT wall-clock time seven hours early.
    Either way the tick is dropped — only the line differs.
    """
    naive = False
    if isinstance(value, str):
        try:
            naive = (
                datetime.fromisoformat(value.strip().replace("Z", "+00:00")).tzinfo
                is None
            )
        except ValueError:
            pass

    if naive:
        reason, message = "naive", (
            "DNSE time %r carries no UTC offset, so the instant it names is "
            "ambiguous; dropping the tick rather than guessing a zone"
        )
    else:
        reason, message = "unparseable", "Unparseable DNSE time %r; dropping the tick"

    count = _TIME_REJECTS.get(reason, 0) + 1
    _TIME_REJECTS[reason] = count
    if count in (1, 100, 10_000) or count % 100_000 == 0:
        log.warning(message + " (%d so far)", value, count)


# DNSE's published Trade-Extra payload carries no message-type field — it is a
# bare market-data object (marketId, boardId, isin, symbol, matchPrice,
# matchQtty, side, avgPrice, ..., time{Seconds,Nanos}). The SDK now recovers the
# type from that shape itself (``TradingClient._infer_msg_type``); this mirrors
# its trade signature for the raw dict the partition works with, since a pure
# function has no client and so no subscription set to consult. These three
# fields are what we actually consume; other channels on the same socket
# (top_price, ohlc, market_index) carry no ``matchPrice`` and are excluded by it.
TRADE_REQUIRED_FIELDS = ("symbol", "matchPrice", "time")
_ENVELOPE_KEYS = ("data", "d", "payload")


def unwrap_frame(frame: dict) -> dict:
    """Return a frame's market-data body, unwrapping an envelope if present."""
    for key in _ENVELOPE_KEYS:
        inner = frame.get(key)
        if isinstance(inner, dict):
            return inner
    return frame


def is_trade_frame(body: dict) -> bool:
    """True when ``body`` looks like a tick / Trade-Extra payload."""
    if body.get("T") == TRADE_EXTRA_MSG_TYPE:
        return True
    return all(body.get(field) is not None for field in TRADE_REQUIRED_FIELDS)


def trade_extra_to_tick_payload(data: dict) -> Optional[dict]:
    """Reshape a decoded Trade-Extra frame into a ``normalize_tick`` API payload.

    Returns None when required fields are missing. DNSE's ``"BUY"``/``"SELL"``
    aggressor side and its protobuf ``time`` ({Seconds, Nanos}, UTC) are mapped
    to the canonical int side and a UTC ISO timestamp here; ``normalize_tick``
    then produces the final canonical tick.

    The SDK's ``TradeExtra`` dataclass covers the same fields but renames them
    (``matchPrice`` -> ``price``) and normalizes ``time`` through the naive
    ``parse_timestamp`` described in ``_time_to_iso``, so the raw dict is read
    directly rather than round-tripped through the model.
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
        # Passed through for `normalize_tick`, which normalizes the spelling and
        # puts it in the ticks table's board_id column. Without it a put-through
        # print is indistinguishable from a continuous-market trade downstream.
        "boardId": data.get("boardId"),
    }


# ---------------------------------------------------------------------------
# SDK adapters
# ---------------------------------------------------------------------------
class _ResilientDecoder(MessageDecoder):
    """A decoder that reports a bad frame instead of raising.

    ``TradingClient._message_handler`` decodes inline and treats any exception
    as fatal when ``auto_reconnect`` is off — so a single malformed frame would
    tear down an otherwise healthy session. Undecodable frames still matter as
    diagnostics (they are the shape of an encoding mismatch, which otherwise
    presents as a subscribed feed delivering no trades), so they are counted by
    the caller and replaced with a frame the dispatcher skips.
    """

    def __init__(self, encoding: str, on_undecodable: Callable[[], None]):
        super().__init__(encoding)
        self._on_undecodable = on_undecodable

    def decode(self, data: bytes) -> Dict[str, Any]:
        try:
            return super().decode(data)
        except Exception:  # noqa: BLE001 - EncodingError and anything under it
            self._on_undecodable()
            return {"action": _UNDECODABLE_ACTION}


class _TradeExtraClient(TradingClient):
    """``TradingClient`` with this feed's dispatch and subscribe behaviour.

    See the module docstring for why each override exists. Everything else —
    connect, TLS, welcome frame, HMAC auth, encoding, heartbeat, the receive
    task and its per-symbol dispatch workers — is inherited unchanged.
    """

    def __init__(self, *args, on_frame: Callable[[dict], Optional[dict]], **kwargs):
        super().__init__(*args, **kwargs)
        self._on_frame = on_frame

    async def subscribe_trade_extra_boards(
        self, boards: List[str], symbols: List[str]
    ) -> None:
        """Subscribe the Trade-Extra channel for each configured board.

        Thin driver over the SDK's own ``subscribe_trade_extra``, which takes a
        single ``board_id`` and defaults to *its* nine-board list when given
        none — not the list configured here, which is why the loop is on this
        side rather than a single call.

        The per-board frame is deliberately the SDK's, not a batched one. Every
        subscribe path upstream sends exactly one channel per frame, and
        ``subscribe_trade_extra``'s own default fans nine boards out as nine
        back-to-back frames — so the loop is the path DNSE ships and exercises.
        Batching them into one frame relies on ``channels`` being a list, which
        is a schema inference nobody has tested against the gateway: if it reads
        only the first entry, the rest are silently unsubscribed and look
        exactly like boards with no trades.
        """
        for board in boards:
            await self.subscribe_trade_extra(
                symbols, encoding=self.encoding, board_id=board
            )

    async def _dispatch_message(self, data: Dict[str, Any]) -> None:
        """Route one decoded frame by shape rather than by ``data["T"]``."""
        action = data.get("action") or data.get("a")
        if action == _UNDECODABLE_ACTION:
            return  # already counted by the decoder
        if action == "pong":
            # An answer to the SDK's heartbeat. DNSE does not appear to send
            # these at all; either way there is nothing to do with one.
            return

        reply = self._on_frame(data)
        if reply is not None:
            await self._connection.send(self._encoder.encode(reply))


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
        # Both encodings are decoded by the SDK's MessageDecoder, but anything
        # else would subscribe successfully and then discard every frame — a
        # feed that looks connected and delivers nothing. Fail at construction
        # rather than at runtime, silently.
        encoding = str(encoding).lower()
        if encoding not in SUPPORTED_ENCODINGS:
            raise RuntimeError(
                f"DNSE_WS_ENCODING={encoding!r} is not supported: use one of "
                f"{', '.join(SUPPORTED_ENCODINGS)}."
            )

        self._api_key = api_key
        self._api_secret = api_secret
        self._symbols = list(symbols)
        self._boards = boards or DEFAULT_BOARDS
        self._base_url = base_url
        self._encoding = encoding
        self._batch_size = batch_size
        self._connect_timeout = connect_timeout
        self._target_desc = f"{base_url} ({len(self._boards)} boards)"

        # Diagnostics for "which boards actually deliver": trades tallied by
        # boardId, and each distinct non-trade action logged once.
        self._board_counts: Dict[str, int] = {}
        self._trades = 0
        self._seen_controls: set[str] = set()
        self._undecodable = 0

        self._q: "queue.Queue[tuple]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        if start:
            self._thread = threading.Thread(
                target=self._run, name="dnse-ws", daemon=True
            )
            self._thread.start()

    # -- decode-side (pure) --------------------------------------------------
    def _process_frame(self, data: dict) -> Optional[dict]:
        """Handle one decoded frame.

        Enqueues matched trades; returns a frame to send back (``pong``) or
        None. The reply is a plain dict, not encoded text — the SDK's encoder
        serialises it, so the same code path is correct under msgpack. Kept
        side-effect-light so tests can call it directly.
        """
        action = data.get("action") or data.get("a")
        if action == "ping":
            return {"action": "pong"}
        body = unwrap_frame(data)
        if is_trade_frame(body):
            payload = trade_extra_to_tick_payload(body)
            if payload is not None:
                self._count_trade(body)
                topic = f"tick_extra/{payload['symbol']}"
                self._q.put((topic, orjson.dumps(payload)))
            return None

        # Neither a trade nor a ping: an ack, a rejection, or a channel nobody
        # asked for. These were dropped without a trace, which is precisely what
        # made a partly-accepted subscription look identical to a set of quiet
        # boards. One line per distinct action keeps a chatty server from
        # flooding the log.
        self._log_control_frame(action, data)
        return None

    def _count_trade(self, body: dict) -> None:
        """Tally trades per board and report the split periodically.

        ``boardId`` is dropped by ``trade_extra_to_tick_payload`` (the canonical
        tick has no board column and the ``ticks`` table no board field), so
        without this there is no way to tell from downstream data whether a
        subscription covering nine boards is delivering all nine. "Only G1
        arrives" is a claim about the feed, and this is where the feed can
        answer it.
        """
        board = str(body.get("boardId") or "?")
        self._board_counts[board] = self._board_counts.get(board, 0) + 1
        self._trades += 1
        if self._trades == 1 or self._trades % _BOARD_REPORT_EVERY == 0:
            log.info(
                "Trade-Extra by board after %d trades: %s",
                self._trades,
                ", ".join(
                    f"{name}={count}"
                    for name, count in sorted(
                        self._board_counts.items(), key=lambda kv: -kv[1]
                    )
                ),
            )

    def _log_control_frame(self, action: Optional[str], data: dict) -> None:
        """Surface a non-trade frame once per distinct action."""
        key = str(action or sorted(data)[:3])
        if key in self._seen_controls:
            return
        self._seen_controls.add(key)
        text = json.dumps(data, default=str)
        if len(text) > _CONTROL_FRAME_CHARS:
            text = text[:_CONTROL_FRAME_CHARS] + "…"
        # A rejected channel is the answer to "why is this board silent", so it
        # is louder than an ordinary ack.
        rejected = any(
            word in key.lower() for word in ("error", "fail", "reject", "denied")
        )
        (log.warning if rejected else log.info)("DNSE control frame: %s", text)

    def _count_undecodable(self) -> None:
        """Note a frame the codec could not read (see ``_ResilientDecoder``)."""
        self._undecodable += 1
        if self._undecodable in (1, 100, 10_000):
            log.warning(
                "%d undecodable frame(s) on %s; the connection is negotiated "
                "as encoding=%s",
                self._undecodable,
                self._target_desc,
                self._encoding,
            )

    # -- async client (background thread) ------------------------------------
    def _run(self) -> None:
        """Reconnect loop: connect, back off on failure, forever.

        There is no clock check. Out of hours DNSE's hostname does not resolve,
        which arrives here as a ``getaddrinfo`` failure like any other outage:
        ``_is_endpoint_unreachable`` keeps it at INFO and the backoff below
        settles at one attempt a minute. That costs a line a minute overnight,
        and buys a loop with no opinion about when the exchange is open — a
        session window is one more thing that can be wrong (a holiday, a
        schedule change, a mis-set ``TZ``) and whose failure mode is a feed that
        stays silent all day.

        The SDK client's own reconnect machinery is switched off in ``_consume``
        so that this is the only backoff in play.
        """
        import asyncio

        backoff = _BACKOFF_START

        while not self._stop.is_set():
            started = time.monotonic()
            try:
                asyncio.run(self._consume())
            except Exception as exc:  # noqa: BLE001 - keep the thread alive
                if _is_endpoint_unreachable(exc):
                    # Unreachable rather than misbehaving: a blip, or the hours
                    # when DNSE simply does not serve the endpoint. Expected
                    # enough not to warrant a warning.
                    log.info(
                        "DNSE WS endpoint unreachable (%s); retrying in %.0fs",
                        exc,
                        backoff,
                    )
                else:
                    # Auth rejected, bad frame, TLS — something that will not fix
                    # itself, and worth surfacing.
                    log.warning(
                        "DNSE WS session ended (%s); reconnecting in %.0fs",
                        exc,
                        backoff,
                    )
            else:
                # A clean return means the server hung up (it does so at the end
                # of the session and across its own restarts) or close() was
                # called. Not an error, but reconnecting without a pause would
                # spin, so it waits like a failure does.
                if not self._stop.is_set():
                    log.info(
                        "DNSE WS closed by the server; reconnecting in %.0fs", backoff
                    )

            if time.monotonic() - started >= _HEALTHY_SESSION_SECS:
                backoff = _BACKOFF_START
            if not self._stop.is_set():
                self._stop.wait(backoff)
            backoff = min(backoff * 2.0, _BACKOFF_CAP)

    def _build_client(self) -> _TradeExtraClient:
        """Construct the SDK client this partition drives.

        ``auto_reconnect`` is off and ``max_retries`` is 1 so that reconnection
        pacing belongs entirely to ``_run``: the SDK's ``WebSocketConnection``
        otherwise retries a failed connect ten times with its own 1..60s
        backoff — roughly eight minutes of dialling a hostname that, out of
        session, does not resolve — before the session gate is consulted again.
        """
        client = _TradeExtraClient(
            api_key=self._api_key,
            api_secret=self._api_secret,
            base_url=self._base_url,
            encoding=self._encoding,
            auto_reconnect=False,
            max_retries=1,
            timeout=self._connect_timeout,
            on_frame=self._process_frame,
        )
        # Swap in a decoder that survives a malformed frame; the SDK builds a
        # strict one whose EncodingError would end the session.
        client._decoder = _ResilientDecoder(self._encoding, self._count_undecodable)
        client.on("error", lambda exc: log.warning("DNSE WS error: %s", exc))
        return client

    async def _consume(self) -> None:
        import asyncio

        log.info(
            "Connecting to DNSE OpenAPI %s/v1/stream (encoding=%s) — "
            "%d symbols, %d boards",
            self._base_url,
            self._encoding,
            len(self._symbols),
            len(self._boards),
        )
        client = self._build_client()
        try:
            # Welcome frame + HMAC auth, per the SDK. Inside the try because a
            # rejected auth raises *after* the socket is up: leaving it outside
            # would strand one open connection per reconnect attempt, and a bad
            # secret retries every 5-60s indefinitely.
            await client.connect()

            # One frame per board, through the SDK — see
            # subscribe_trade_extra_boards for why not batched. Whether the
            # server accepted them all is answered by the control frames
            # _process_frame logs, not by this line.
            await client.subscribe_trade_extra_boards(self._boards, self._symbols)
            log.info(
                "Subscribed Trade-Extra on %d boards: %s",
                len(self._boards),
                ",".join(self._boards),
            )

            # The SDK owns the receive loop, so returning here means either
            # close() was called or its message handler stopped — a server-side
            # close, or an unrecoverable error it already emitted. Both are
            # handled by the reconnect loop in _run.
            #
            # A socket that is open but silent is not checked for: the pong
            # clock that would answer it is meaningless on a gateway that never
            # pongs (see _dispatch_message), and the ``websockets`` protocol
            # ping — 30s interval, 30s timeout, set in the SDK's
            # WebSocketConnection — already tears down a dead connection.
            handler = client._message_handler_task
            while not self._stop.is_set() and not handler.done():
                await asyncio.sleep(_SHUTDOWN_POLL_SECS)
        finally:
            await client.disconnect()

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
@dataclass
class DnseTradeSource(DynamicSource):
    """Bytewax source over DNSE OpenAPI's Trade-Extra WebSocket feed.

    Emits ``(topic, payload_bytes)`` per matched trade — a payload shaped for
    ``core.tick_contract.normalize_tick``. One partition per worker.

    Every field is forwarded verbatim to ``DnseTradePartition``, which is where
    they are documented and validated; the names must therefore match its
    parameters.
    """

    api_key: str
    api_secret: str
    symbols: List[str]
    boards: Optional[List[str]] = None
    base_url: str = DEFAULT_BASE_URL
    encoding: str = "json"
    batch_size: int = 1024

    def build(self, _step_id: str, _worker_index: int, _worker_count: int):
        return DnseTradePartition(**vars(self))
