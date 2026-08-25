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
  4. consume frames; a Trade-Extra payload is a bare market-data object —
     ``marketId``, ``boardId``, ``isin``, ``symbol``, ``matchPrice``,
     ``matchQtty``, ``side``, ``avgPrice``, …, ``time {Seconds, Nanos}`` —
     with **no message-type field**, so trades are matched on shape
     (see ``is_trade_frame``) rather than on a marker
  5. answer server ``ping`` frames with ``pong`` (the server pings every 3
     minutes and closes the socket if unanswered within 1 minute)

The gateway SDK is fully async; Bytewax pulls synchronously via ``next_batch``.
The partition therefore runs the asyncio client in a background thread and hands
messages to the runtime through a thread-safe queue. Each matched trade is
reshaped into the API-style payload that ``core.tick_contract.normalize_tick``
already parses and emitted as ``(topic, payload_bytes)`` — the exact contract the
MQTT tick source used, so ``workers.tick_ingest`` and
``workers.block_episode_ingest`` need no per-source handling.

Boards ``G1`` (even lot) and ``G4`` (odd lot) carry both stocks and derivatives,
so a single subscription covers equities and the VN30F futures contract.

The endpoint is **only served during the exchange session**. Out of hours
``ws-openapi.dnse.com.vn`` stops resolving altogether, so a connect attempt fails
in ``getaddrinfo`` with ``[Errno -2] Name or service not known`` rather than
anything protocol-shaped. The reconnect loop therefore consults the clock first
and sleeps until the next open (see ``seconds_until_session``), which is what
keeps an overnight worker from logging a DNS failure every few seconds.

Credentials come from ``DNSE_API_KEY`` / ``DNSE_API_SECRET`` and are never logged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import queue
import socket
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from typing import List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    worth a warning. ``websockets`` wraps connect failures in its own exception,
    so the ``__cause__``/``__context__`` chain is walked rather than just the
    outermost type.
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


# DNSE's published Trade-Extra payload carries no message-type field — it is a
# bare market-data object (marketId, boardId, isin, symbol, matchPrice,
# matchQtty, side, avgPrice, ..., time{Seconds,Nanos}). Gating on a marker such
# as ``T == "te"`` therefore drops the *entire* feed silently, so trades are
# recognised by shape instead. These three fields are what we actually consume;
# other channels on the same socket (top_price, ohlc, market_index) carry no
# ``matchPrice`` and are excluded by it.
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
        encoding: str = "msgpack",
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
        self._api_key = api_key
        self._api_secret = api_secret
        self._symbols = list(symbols)
        self._boards = boards or DEFAULT_BOARDS
        self._base_url = base_url
        self._encoding = encoding
        self._batch_size = batch_size
        self._connect_timeout = connect_timeout

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
    def _process_frame(self, data: dict) -> Optional[str]:
        """Handle one decoded frame.

        Enqueues matched trades; returns a reply string to send back (``pong``)
        or None. Kept side-effect-light so tests can call it directly.
        """
        action = data.get("action") or data.get("a")
        if action == "ping":
            return json.dumps({"action": "pong"})
        body = unwrap_frame(data)
        if is_trade_frame(body):
            payload = trade_extra_to_tick_payload(body)
            if payload is not None:
                topic = f"tick_extra/{payload['symbol']}"
                self._q.put((topic, orjson.dumps(payload)))
        return None

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
        the next open turns that into one line per evening.
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
