"""DNSE OpenAPI market-data WebSocket source for Bytewax (Trade Extra).

Transport, authentication, encoding and heartbeat are delegated to the official
DNSE SDK vendored at ``worker/dnse_sdk`` (``dnse.websocket.TradingClient``) —
inside the worker tree, so it is an ordinary top-level import that needs no
sys.path shim and travels with the image. This module supplies only the parts
the SDK gets wrong or does not cover for this feed, and adapts the result to
Bytewax.

What the SDK now owns
---------------------
* the ``wss://ws-openapi.dnse.com.vn/v1/stream?encoding=…`` connect, TLS and
  welcome-frame handshake (``WebSocketConnection``)
* HMAC-SHA256 auth over ``{api_key}:{ts}:{nonce}`` (``AuthManager``)
* JSON **and MessagePack** framing (``MessageEncoder`` / ``MessageDecoder``) —
  which is why ``DNSE_WS_ENCODING=msgpack`` is now a supported setting rather
  than a construction-time error
* the application-level heartbeat and the receive/dispatch task fan-out

Fixed in the SDK, not worked around here
-----------------------------------------
Two upstream defects are repaired in ``worker/dnse_sdk`` itself, both marked
``LOCAL PATCH`` in the source, because both governed every channel rather than
just this one. Re-vendoring the SDK reverts them; the tests under
"Vendored-SDK patch" in ``tests/test_dnse_ws_input.py`` are what will say so.

**Timestamps.** ``models.parse_timestamp`` built every datetime with a bare
``fromtimestamp()`` and formatted with ``strftime``, dropping any offset — so
the protobuf and unix branches came out in the *host's* zone and a string came
out in whichever zone it arrived in, all three indistinguishable once returned.
It now resolves to UTC and keeps the offset, and refuses to guess a naive
timestamp. ``_time_to_iso`` below delegates to it and adds only a visible,
throttled rejection.

**Message routing.**
DNSE's Trade-Extra payload is a *bare* market-data object — ``marketId``,
``boardId``, ``isin``, ``symbol``, ``matchPrice``, ``matchQtty``, ``side``,
``avgPrice``, …, ``time {Seconds, Nanos}`` — with no message-type field at all,
and upstream routed market data solely on ``data["T"]`` with no ``else`` on the
chain. Subscribing to tick_extra therefore produced a connection that passed
every health signal and delivered nothing.

That is repaired in ``dnse_sdk/dnse/websocket/client.py`` (``_infer_msg_type``
plus the missing ``else``) rather than worked around here, so it holds for every
channel — ``top_price``, ``ohlc`` and the rest were equally affected. The local
``is_trade_frame`` applies the same rule to the raw dict, which is what this
partition sees.

What this module still owns, and why
------------------------------------
``_TradeExtraClient`` subclasses ``TradingClient`` for one behaviour that is a
choice for this ingester rather than an SDK defect:

* **Reconnection is session-gated.** The SDK's own reconnect loop knows nothing
  about exchange hours, so it is disabled (``auto_reconnect=False``,
  ``max_retries=1``) and ``_run`` below owns the pacing.

Subscription uses the SDK's ``subscribe_trade_extra``, one frame per board, only
driven from the configured board list (its own ``board_id=None`` default means
*its* nine boards, not ours). An earlier version batched every board into a
single frame; that relied on ``channels`` being a list, which no upstream code
path exercises — every subscribe in the SDK sends exactly one channel, and
``subscribe_trade_extra`` fans its nine-board default out as nine separate
frames. Batching was an untested inference whose failure mode, a gateway reading
only ``channels[0]``, is silent and looks exactly like quiet boards.

Frames are still consumed as raw dicts rather than through ``TradeExtra``: the
partition reshapes them for ``normalize_tick`` and logs unrecognised control
frames, and the model's renames (``matchPrice`` -> ``price``) would only have to
be undone. That is a fit question now, not a correctness one — the model's
``time`` is trustworthy since the patch above.

Each matched trade is reshaped into the API-style payload that
``core.tick_contract.normalize_tick`` already parses and emitted as
``(topic, payload_bytes)`` — the exact contract the MQTT tick source used, so
``workers.tick_ingest`` and ``workers.block_episode_ingest`` need no per-source
handling.

Boards ``G1`` (even lot) and ``G4`` (odd lot) carry both stocks and derivatives,
so a single subscription covers equities and the VN30F futures contract.

The endpoint is **only served during the exchange session**. Out of hours
``ws-openapi.dnse.com.vn`` stops resolving altogether, so a connect attempt fails
in ``getaddrinfo`` with ``[Errno -2] Name or service not known`` rather than
anything protocol-shaped. The reconnect loop therefore consults the clock first
and sleeps until the next open (see ``seconds_until_session``), which is what
keeps an overnight worker from logging a DNS failure every few seconds.

The gateway SDK is fully async; Bytewax pulls synchronously via ``next_batch``.
The partition therefore runs the asyncio client in a background thread and hands
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
from datetime import datetime, timedelta
from datetime import time as dtime
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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

# The window the socket is attempted in, wide enough to cover the pre-open
# auction through the post-close run-off rather than only continuous trading —
# the point is to skip the hours when the host is simply gone, not to police the
# session. Vietnam observes no DST, so a wall-clock window needs no offset care.
DEFAULT_SESSION_TZ = "Asia/Ho_Chi_Minh"
DEFAULT_SESSION_START = "08:00"
DEFAULT_SESSION_END = "16:00"

# Longest single sleep while waiting for the next open. ``Event.wait`` returns as
# soon as ``close()`` sets the flag, so this is not about shutdown latency: it
# keeps a wrong clock, a stale tz database, or a host resumed from suspend from
# parking the thread for hours, and leaves a heartbeat in the log.
_SESSION_WAIT_CAP = 900.0

# In-session reconnect backoff. Starts where the old fixed delay was and doubles
# up to the cap, so a mid-session blip still recovers in seconds while a broken
# endpoint is retried once a minute instead of twelve times.
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

# How often the connection is asked whether it has gone stale. Far slower than
# the shutdown poll on purpose: ``TradingClient.is_healthy`` emits a warning
# every time it finds a stale pong clock, so evaluating it at 4Hz would fill the
# log with the same line before the reconnect it triggers even happened.
_HEALTH_CHECK_SECS = 10.0

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
def parse_hhmm(text: str, fallback: str) -> dtime:
    """Parse an ``"HH:MM"`` session bound, falling back rather than crashing.

    A typo in ``DNSE_WS_SESSION_START`` should not stop the ingester from
    starting — losing the gate costs some log noise out of hours, while raising
    here would cost the whole feed.
    """
    try:
        hour, _, minute = str(text).strip().partition(":")
        return dtime(int(hour), int(minute or 0))
    except (TypeError, ValueError):
        log.warning("Unparseable session time %r; using %s", text, fallback)
        hour, _, minute = fallback.partition(":")
        return dtime(int(hour), int(minute))


def seconds_until_session(
    now: datetime,
    start: dtime,
    end: dtime,
    weekdays_only: bool = True,
) -> float:
    """Seconds until the exchange session opens; ``0.0`` when it is open now.

    ``now`` must be timezone-aware **in the exchange's own zone**, so that the
    comparison is against Ho Chi Minh wall-clock time however the container is
    configured. Weekends count as closed.

    Public holidays are deliberately not modelled — the exchange publishes no
    machine-readable calendar this worker can follow — so a holiday still opens
    the socket and lands in the ordinary in-session backoff. That is the safe
    direction to be wrong in: a missed session costs ticks that cannot be
    recovered from a stream, while an extra connect attempt costs one log line.
    """
    open_at = now.replace(
        hour=start.hour, minute=start.minute, second=0, microsecond=0
    )
    close_at = now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    trading_day = not weekdays_only or now.weekday() < 5

    if trading_day and open_at <= now < close_at:
        return 0.0

    # Either side of today's window: before it, today's open still counts;
    # after it (or on a weekend), scan forward for the next trading day.
    candidate = open_at if trading_day and now < open_at else open_at + timedelta(days=1)
    for _ in range(8):
        if not weekdays_only or candidate.weekday() < 5:
            return max(0.0, (candidate - now).total_seconds())
        candidate += timedelta(days=1)
    return _SESSION_WAIT_CAP  # unreachable: a week always contains a weekday


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


def trade_extra_channel(board: str, encoding: str = "json") -> str:
    """Trade-Extra channel name for a board, e.g. ``tick_extra.G1.json``.

    Mirrors what ``TradingClient.subscribe_trade_extra`` builds. The SDK sends
    the subscribe frames; this is here so tests and logs can name a channel
    without reaching into it.
    """
    return f"tick_extra.{board}.{'msgpack' if encoding == 'msgpack' else 'json'}"


def _time_to_iso(value) -> Optional[str]:
    """DNSE ``time`` (ISO str, protobuf {Seconds,Nanos}, or unix s/ms) -> UTC ISO.

    The conversion itself is the SDK's ``models.parse_timestamp``, patched there
    to return an explicit UTC offset for every input shape rather than the naive
    local string it used to build (see ``dnse_sdk/__init__.py``). Every value
    returned here therefore carries an offset, which is the whole contract: the
    instant a tick happened is not recoverable once it has been written to
    ``ticks`` under the wrong zone, and nothing downstream can tell a wrong
    timestamp from a right one.

    This wrapper adds only what a library cannot: a *visible* rejection.
    ``parse_timestamp`` returns None for anything it will not place on the
    timeline — including a naive string, whose zone it refuses to guess — and a
    tick dropped in silence at several thousand a second is precisely the
    failure mode that is hardest to notice.
    """
    if value is None:
        return None
    iso = parse_timestamp(value)
    if iso is not None:
        return iso
    _warn_rejected_time(*_classify_rejected_time(value))
    return None


def _classify_rejected_time(value) -> tuple:
    """``(reason, message, value)`` for a time ``parse_timestamp`` refused.

    Only the log line differs; both cases drop the tick. A naive string is
    called out separately because it is the one that looks fine — it parses,
    it just does not say *when*, and ``normalize_tick`` would read the missing
    offset as UTC and land an ICT wall-clock time seven hours early.
    """
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is None:
            return (
                "naive",
                "DNSE time %r carries no UTC offset, so the instant it names is "
                "ambiguous; dropping the tick rather than guessing a zone",
                value,
            )
    return ("unparseable", "Unparseable DNSE time %r; dropping the tick", value)


# Rejected-timestamp tallies, keyed by reason. If the feed ever does change
# shape, every tick in the session hits the same branch — so this reports at a
# widening interval (like the undecodable-frame counter) instead of emitting one
# warning per trade at several thousand a second.
_TIME_REJECTS: Dict[str, int] = {}
_REJECT_REPORT_AT = (1, 100, 10_000)


def _warn_rejected_time(reason: str, message: str, value) -> None:
    count = _TIME_REJECTS.get(reason, 0) + 1
    _TIME_REJECTS[reason] = count
    if count in _REJECT_REPORT_AT or count % 100_000 == 0:
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
        # Whether this gateway has ever answered one of our heartbeat pings.
        # See is_stalled for why the health check hinges on it.
        self._pong_seen = False

    def is_stalled(self) -> bool:
        """True when the session should be torn down and rebuilt.

        ``TradingClient.is_healthy`` fails a connection that has not seen a
        ``pong`` within twice the heartbeat interval, which only means anything
        if the server answers client pings at all. The protocol DNSE documents
        runs the other way — the *server* pings every three minutes and drops
        the socket if we do not pong within one — and says nothing about it
        replying to ours. If it never does, an unguarded ``is_healthy`` reads
        false 50s into every connection and turns the reconnect loop into a
        storm, which is far worse than the stall it is meant to catch.

        So the pong clock is trusted only once a pong has actually arrived;
        until then this falls back to what can be checked without it.
        """
        connection = self._connection
        if connection is None or not connection.is_connected:
            return True
        if not self._is_authenticated:
            return True
        if not self._pong_seen:
            return False  # no evidence this gateway pongs; clock is meaningless
        return not self.is_healthy

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
            # Keeps ``is_healthy`` meaningful; not interesting to log.
            self._last_pong_time = time.time()
            self._pong_seen = True
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
        session_tz: str = DEFAULT_SESSION_TZ,
        session_start: str = DEFAULT_SESSION_START,
        session_end: str = DEFAULT_SESSION_END,
        session_gate: bool = True,
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

        # An unknown zone name would otherwise take the whole worker down at
        # build time; the gate is a convenience, so it degrades to the exchange
        # default instead.
        self._session_gate = session_gate
        try:
            self._session_tz = ZoneInfo(session_tz)
        except (ZoneInfoNotFoundError, ValueError):
            log.warning(
                "Unknown session timezone %r; using %s",
                session_tz,
                DEFAULT_SESSION_TZ,
            )
            self._session_tz = ZoneInfo(DEFAULT_SESSION_TZ)
        self._session_start = parse_hhmm(session_start, DEFAULT_SESSION_START)
        self._session_end = parse_hhmm(session_end, DEFAULT_SESSION_END)

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

    # -- session gate --------------------------------------------------------
    def seconds_until_open(self) -> float:
        """Seconds until the feed is expected to be served; 0.0 when it is now."""
        if not self._session_gate:
            return 0.0
        return seconds_until_session(
            datetime.now(self._session_tz), self._session_start, self._session_end
        )

    # -- async client (background thread) ------------------------------------
    def _run(self) -> None:
        """Reconnect loop: wait for the session, connect, back off on failure.

        The clock is consulted before every attempt because DNSE only serves the
        endpoint during the session — out of hours its hostname does not resolve,
        and a loop that reconnected regardless spent each night emitting
        ``[Errno -2] Name or service not known`` every five seconds. Waiting for
        the next open turns that into one line per evening. This is also why the
        SDK client's own reconnect machinery is switched off in ``_consume``:
        it would dial straight through a closed exchange.
        """
        import asyncio

        backoff = _BACKOFF_START
        announced_closed = False

        while not self._stop.is_set():
            wait = self.seconds_until_open()
            if wait > 0.0:
                # Logged once per closure, not once per sleep: the cap below can
                # wake this loop many times before the market opens.
                if not announced_closed:
                    log.info(
                        "Outside the %s-%s %s session; DNSE serves the feed only "
                        "during it, so waiting %.1fh for the next open",
                        self._session_start.strftime("%H:%M"),
                        self._session_end.strftime("%H:%M"),
                        self._session_tz.key,
                        wait / 3600.0,
                    )
                    announced_closed = True
                self._stop.wait(min(wait, _SESSION_WAIT_CAP))
                continue

            announced_closed = False
            started = time.monotonic()
            try:
                asyncio.run(self._consume())
            except Exception as exc:  # noqa: BLE001 - keep the thread alive
                if _is_endpoint_unreachable(exc):
                    # In-session and unreachable: a blip, or the session ended
                    # early / a holiday the gate cannot know about. Expected
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
            # close() was called, its message handler stopped (a server-side
            # close, or an unrecoverable error it already emitted), or the
            # connection went quiet while still nominally open. All three are
            # handled by the reconnect loop in _run.
            handler = client._message_handler_task
            next_health_check = time.monotonic() + _HEALTH_CHECK_SECS
            while not self._stop.is_set() and not handler.done():
                await asyncio.sleep(_SHUTDOWN_POLL_SECS)
                now = time.monotonic()
                if now < next_health_check:
                    continue
                next_health_check = now + _HEALTH_CHECK_SECS
                if client.is_stalled():
                    # An open socket delivering nothing looks exactly like a
                    # quiet market from the outside, so nothing else in the
                    # pipeline would ever notice this.
                    log.warning(
                        "DNSE WS is up but stalled (no pong within %.0fs); "
                        "dropping the session so it can be rebuilt",
                        client.heartbeat_interval * 2,
                    )
                    return
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
        session_tz: str = DEFAULT_SESSION_TZ,
        session_start: str = DEFAULT_SESSION_START,
        session_end: str = DEFAULT_SESSION_END,
        session_gate: bool = True,
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._symbols = symbols
        self._boards = boards
        self._base_url = base_url
        self._encoding = encoding
        self._batch_size = batch_size
        self._session_tz = session_tz
        self._session_start = session_start
        self._session_end = session_end
        self._session_gate = session_gate

    def build(self, _step_id: str, _worker_index: int, _worker_count: int):
        return DnseTradePartition(
            self._api_key,
            self._api_secret,
            self._symbols,
            boards=self._boards,
            base_url=self._base_url,
            encoding=self._encoding,
            batch_size=self._batch_size,
            session_tz=self._session_tz,
            session_start=self._session_start,
            session_end=self._session_end,
            session_gate=self._session_gate,
        )
