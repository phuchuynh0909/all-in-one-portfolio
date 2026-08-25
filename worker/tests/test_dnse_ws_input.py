"""Offline tests for the DNSE OpenAPI Trade-Extra WebSocket source.

No network: the protocol is exercised through its pure helpers and the
partition's ``_process_frame`` / ``next_batch`` (built with ``start=False`` so
no socket opens). Verifies HMAC auth framing, channel/subscribe construction,
the Trade-Extra -> canonical-tick normalization (round-tripped through
``normalize_tick``), ping/pong, and the credential guard.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
from datetime import datetime
from datetime import time as dtime
from zoneinfo import ZoneInfo

import orjson
import pytest

from infra.dnse_ws_input import (
    DEFAULT_SESSION_TZ,
    DnseTradePartition,
    build_auth_message,
    build_subscribe_message,
    compute_signature,
    normalize_side,
    parse_hhmm,
    seconds_until_session,
    trade_extra_channel,
    trade_extra_to_tick_payload,
    is_trade_frame,
    unwrap_frame,
    _is_endpoint_unreachable,
    _time_to_iso,
)
from core.tick_contract import normalize_tick, SIDE_BUY, SIDE_SELL, SIDE_UNKNOWN


# ---------------------------------------------------------------------------
# HMAC auth
# ---------------------------------------------------------------------------
def test_compute_signature_matches_reference():
    sig = compute_signature("key", "secret", 1_700_000_000, "12345")
    expected = hmac.new(
        b"secret", b"key:1700000000:12345", hashlib.sha256
    ).hexdigest()
    assert sig == expected


def test_build_auth_message_is_self_consistent():
    msg = build_auth_message("mykey", "mysecret", now=1_700_000_000.5)
    assert msg["action"] == "auth"
    assert msg["api_key"] == "mykey"
    assert msg["timestamp"] == 1_700_000_000
    assert msg["nonce"] == str(int(1_700_000_000.5 * 1_000_000))
    assert msg["signature"] == compute_signature(
        "mykey", "mysecret", msg["timestamp"], msg["nonce"]
    )


# ---------------------------------------------------------------------------
# Channel / subscribe framing
# ---------------------------------------------------------------------------
def test_trade_extra_channel():
    assert trade_extra_channel("G1") == "tick_extra.G1.json"
    assert trade_extra_channel("T2", "msgpack") == "tick_extra.T2.msgpack"


def test_build_subscribe_message():
    msg = build_subscribe_message("tick_extra.G1.json", ["FPT", "HPG"])
    assert msg == {
        "action": "subscribe",
        "channels": [{"name": "tick_extra.G1.json", "symbols": ["FPT", "HPG"]}],
    }


# ---------------------------------------------------------------------------
# time normalization
# ---------------------------------------------------------------------------
def test_time_to_iso_variants():
    assert _time_to_iso(None) is None
    assert _time_to_iso("2026-06-22T03:00:00Z") == "2026-06-22T03:00:00Z"
    # protobuf {Seconds, Nanos}
    assert _time_to_iso({"Seconds": 1_781_000_000, "Nanos": 0}).endswith("+00:00")
    # unix seconds vs milliseconds resolve to the same instant
    secs = _time_to_iso(1_781_000_000)
    ms = _time_to_iso(1_781_000_000_000)
    assert secs == ms


# ---------------------------------------------------------------------------
# Trade-Extra -> canonical tick (via normalize_tick)
# ---------------------------------------------------------------------------
def _te_frame(symbol="FPT", price=100.0, qty=1.0, side="BUY", seconds=1_779_766_822):
    # Mirrors the documented DNSE Trade-Extra frame: side is "BUY"/"SELL",
    # time is a protobuf {Seconds, Nanos} in UTC.
    return {
        "T": "te",
        "marketId": "DVX",
        "boardId": "G1",
        "isin": "VN41I1G60005",
        "symbol": symbol,
        "matchPrice": price,
        "matchQtty": qty,
        "side": side,
        "avgPrice": 2023.92,
        "time": {"Seconds": seconds, "Nanos": 72_000_000},
    }


def test_normalize_side_maps_dnse_strings():
    assert normalize_side("BUY") == SIDE_BUY
    assert normalize_side("SELL") == SIDE_SELL
    assert normalize_side("buy") == SIDE_BUY
    assert normalize_side(1) == SIDE_BUY
    assert normalize_side("weird") == SIDE_UNKNOWN


def test_trade_extra_payload_normalizes_to_canonical_tick():
    payload = trade_extra_to_tick_payload(_te_frame(side="BUY", price=2022.5, qty=1.0))
    assert payload is not None
    assert payload["side"] == SIDE_BUY  # "BUY" mapped before normalize_tick
    tick = normalize_tick(payload)
    assert tick is not None
    assert tick["symbol"] == "FPT"
    assert tick["match_price"] == 2022.5
    assert tick["match_qty"] == 1
    assert tick["side"] == SIDE_BUY
    # protobuf time -> UTC instant preserved
    assert tick["sending_time"].astimezone().timestamp() == pytest.approx(
        1_779_766_822.072, abs=1e-3
    )


def test_trade_extra_sell_side():
    tick = normalize_tick(trade_extra_to_tick_payload(_te_frame(side="SELL")))
    assert tick["side"] == SIDE_SELL


def test_trade_extra_missing_fields_returns_none():
    assert trade_extra_to_tick_payload({"T": "te", "symbol": "FPT"}) is None  # no price/time
    assert trade_extra_to_tick_payload({"T": "te", "matchPrice": 1, "time": 1}) is None  # no symbol


# ---------------------------------------------------------------------------
# Partition frame handling (no socket)
# ---------------------------------------------------------------------------
def _partition(**kwargs):
    return DnseTradePartition("key", "secret", ["FPT", "HPG"], start=False, **kwargs)


def test_process_frame_enqueues_trade_extra():
    part = _partition()
    reply = part._process_frame(_te_frame(symbol="FPT", price=101.5, qty=300, side="SELL"))
    assert reply is None
    batch = part.next_batch()
    assert len(batch) == 1
    topic, payload = batch[0]
    assert topic == "tick_extra/FPT"
    decoded = orjson.loads(payload)
    assert decoded["symbol"] == "FPT"
    assert decoded["matchPrice"] == 101.5
    # Round-trips through the shared normalizer.
    tick = normalize_tick(decoded)
    assert tick["side"] == SIDE_SELL and tick["match_qty"] == 300


def test_process_frame_answers_ping_with_pong():
    part = _partition()
    reply = part._process_frame({"action": "ping"})
    assert json.loads(reply) == {"action": "pong"}
    assert part.next_batch() == []  # ping is not enqueued


def test_process_frame_ignores_other_message_types():
    part = _partition()
    assert part._process_frame({"T": "q", "symbol": "FPT"}) is None  # a quote
    assert part._process_frame({"action": "subscribed"}) is None
    assert part.next_batch() == []


def test_next_batch_respects_batch_size():
    part = _partition(batch_size=2)
    for i in range(5):
        part._process_frame(_te_frame(symbol=f"S{i}"))
    assert len(part.next_batch()) == 2
    assert len(part.next_batch()) == 2
    assert len(part.next_batch()) == 1
    assert part.next_batch() == []


def test_missing_credentials_raises():
    with pytest.raises(RuntimeError, match="credentials missing"):
        DnseTradePartition("", "", ["FPT"], start=False)


# ---------------------------------------------------------------------------
# Shape-based trade detection
#
# The published Trade-Extra payload carries NO message-type field. Gating on a
# marker (an earlier `T == "te"` check) silently dropped the entire feed, so
# these lock the real payload shape in.
# ---------------------------------------------------------------------------
DOCS_TRADE_EXTRA_FRAME = {
    "marketId": "DVX",
    "boardId": "G1",
    "isin": "VN41I1G60005",
    "symbol": "41I1G6000",
    "matchPrice": 2022.5,
    "matchQtty": 1.0,
    "side": "SELL",
    "avgPrice": 2023.92,
    "totalVolumeTraded": 55913,
    "grossTradeAmount": 11316.34193,
    "highestPrice": 2028.0,
    "lowestPrice": 2018.3,
    "openPrice": 2018.6,
    "tradingSessionId": "40",
    "time": {"Seconds": 1779766822, "Nanos": 72000000},
}


def test_documented_payload_is_recognised_without_type_marker():
    assert "T" not in DOCS_TRADE_EXTRA_FRAME
    assert is_trade_frame(DOCS_TRADE_EXTRA_FRAME) is True


def test_documented_payload_reaches_the_queue_and_normalizes():
    part = _partition()
    assert part._process_frame(DOCS_TRADE_EXTRA_FRAME) is None
    batch = part.next_batch()
    assert len(batch) == 1
    topic, payload = batch[0]
    assert topic == "tick_extra/41I1G6000"
    tick = normalize_tick(orjson.loads(payload))
    assert tick["symbol"] == "41I1G6000"
    assert tick["match_price"] == 2022.5
    assert tick["side"] == SIDE_SELL
    # Float matchQtty (1.0) must land as an int for the Int64 column.
    assert tick["match_qty"] == 1 and isinstance(tick["match_qty"], int)
    assert tick["sending_time"].year == 2026


def test_trade_frame_requires_a_match_price():
    """Other channels on the same socket must not be mistaken for trades."""
    top_price = {
        "symbol": "HPG",
        "boardId": "G1",
        "time": {"Seconds": 1779766822, "Nanos": 0},
        "bidPrices": [{"price": 24.3, "qtty": 100}],
    }
    ohlc = {"symbol": "HPG", "open": 24.2, "close": 24.35,
            "time": {"Seconds": 1779766822, "Nanos": 0}}
    assert is_trade_frame(top_price) is False
    assert is_trade_frame(ohlc) is False
    part = _partition()
    for frame in (top_price, ohlc, {"action": "subscribe_success"}):
        part._process_frame(frame)
    assert part.next_batch() == []


def test_unwrap_frame_handles_envelope_and_bare_payloads():
    assert unwrap_frame(DOCS_TRADE_EXTRA_FRAME) is DOCS_TRADE_EXTRA_FRAME
    for key in ("data", "d", "payload"):
        wrapped = {"channel": "tick_extra.G1.json", key: DOCS_TRADE_EXTRA_FRAME}
        assert unwrap_frame(wrapped) is DOCS_TRADE_EXTRA_FRAME


def test_enveloped_trade_frame_is_enqueued():
    part = _partition()
    part._process_frame({"channel": "tick_extra.G1.json", "data": DOCS_TRADE_EXTRA_FRAME})
    assert len(part.next_batch()) == 1


# ---------------------------------------------------------------------------
# Session gate
#
# DNSE serves the WebSocket endpoint only while the exchange is open; out of
# hours ws-openapi.dnse.com.vn does not resolve, so an ungated reconnect loop
# logged "[Errno -2] Name or service not known" every 5s all night. These pin
# the window the socket is attempted in.
# ---------------------------------------------------------------------------
TZ = ZoneInfo(DEFAULT_SESSION_TZ)
OPEN_AT = dtime(8, 0)
CLOSE_AT = dtime(16, 0)


def _ict(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


def _wait(now):
    return seconds_until_session(now, OPEN_AT, CLOSE_AT)


def test_inside_session_does_not_wait():
    # 2026-08-25 is a Tuesday.
    assert _wait(_ict(2026, 8, 25, 8, 0)) == 0.0  # at the bell
    assert _wait(_ict(2026, 8, 25, 11, 30)) == 0.0
    assert _wait(_ict(2026, 8, 25, 15, 59)) == 0.0


def test_before_the_open_waits_until_todays_open():
    assert _wait(_ict(2026, 8, 25, 6, 0)) == 2 * 3600.0
    assert _wait(_ict(2026, 8, 25, 0, 0)) == 8 * 3600.0


def test_after_the_close_waits_until_tomorrow():
    # The close is exclusive: 16:00 sharp is already out of session.
    assert _wait(_ict(2026, 8, 25, 16, 0)) == 16 * 3600.0
    assert _wait(_ict(2026, 8, 25, 22, 0)) == 10 * 3600.0


def test_weekend_waits_until_monday():
    # 2026-08-29 is a Saturday, 2026-08-30 a Sunday.
    saturday = _wait(_ict(2026, 8, 29, 10, 0))
    assert saturday == (2 * 24 - 10 + 8) * 3600.0  # Monday 08:00
    sunday = _wait(_ict(2026, 8, 30, 10, 0))
    assert sunday == (24 - 10 + 8) * 3600.0
    # Friday evening rolls over the weekend too (2026-08-28 is a Friday).
    assert _wait(_ict(2026, 8, 28, 17, 0)) == (3 * 24 - 17 + 8) * 3600.0


def test_partition_gate_reports_open_and_closed(monkeypatch):
    part = _partition(session_start="08:00", session_end="16:00")

    class _FrozenDatetime(datetime):
        frozen = _ict(2026, 8, 25, 21, 0)

        @classmethod
        def now(cls, tz=None):
            return cls.frozen.astimezone(tz) if tz else cls.frozen

    monkeypatch.setattr("infra.dnse_ws_input.datetime", _FrozenDatetime)
    assert part.seconds_until_open() == 11 * 3600.0  # closed until 08:00
    _FrozenDatetime.frozen = _ict(2026, 8, 25, 9, 0)
    assert part.seconds_until_open() == 0.0  # open


def test_session_gate_can_be_disabled():
    part = _partition(session_gate=False)
    assert part.seconds_until_open() == 0.0  # any hour is fair game


def test_parse_hhmm_falls_back_instead_of_raising():
    assert parse_hhmm("09:15", "08:00") == dtime(9, 15)
    assert parse_hhmm("9", "08:00") == dtime(9, 0)
    assert parse_hhmm("garbage", "08:00") == dtime(8, 0)
    assert parse_hhmm(None, "16:30") == dtime(16, 30)


def test_bad_session_timezone_degrades_to_the_default():
    part = _partition(session_tz="Mars/Olympus_Mons")
    assert part._session_tz.key == DEFAULT_SESSION_TZ


def test_dns_failure_is_classified_as_unreachable():
    """The reported error must read as "endpoint absent", not a protocol fault."""
    gai = socket.gaierror(-2, "Name or service not known")
    assert _is_endpoint_unreachable(gai) is True
    # websockets wraps connect failures, so the cause chain has to be walked.
    wrapped = OSError("connect failed")
    wrapped.__cause__ = gai
    assert _is_endpoint_unreachable(wrapped) is True
    assert _is_endpoint_unreachable(ConnectionResetError()) is True
    assert _is_endpoint_unreachable(TimeoutError()) is True
    # An auth rejection is a real problem and must stay a warning.
    assert _is_endpoint_unreachable(RuntimeError("DNSE OpenAPI auth failed")) is False


def _drive_run(part, stop_after):
    """Run the reconnect loop for ``stop_after`` sleeps, recording each delay.

    ``_stop.wait`` is the loop's only blocking point, so standing in for it both
    makes the loop finite and reveals the pacing it would have used.
    """
    delays = []
    real_wait = part._stop.wait

    def fake_wait(timeout=None):
        delays.append(timeout)
        if len(delays) >= stop_after:
            part._stop.set()
        return real_wait(0)

    part._stop.wait = fake_wait  # type: ignore[method-assign]
    part._run()
    return delays


def test_run_does_not_connect_outside_the_session(monkeypatch):
    """The whole point: no socket is attempted while the endpoint is unserved."""
    part = _partition()
    attempts = []
    monkeypatch.setattr(
        part, "_consume", lambda: attempts.append(1), raising=False
    )
    monkeypatch.setattr(part, "seconds_until_open", lambda: 11 * 3600.0)

    delays = _drive_run(part, stop_after=2)
    assert attempts == []  # never dialled
    # Sleeps are capped so the loop keeps a heartbeat rather than parking 11h.
    assert delays == [900.0, 900.0]


def test_run_backs_off_exponentially_on_in_session_failures(monkeypatch):
    part = _partition()
    monkeypatch.setattr(part, "seconds_until_open", lambda: 0.0)

    def boom():
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(part, "_consume", boom, raising=False)
    # asyncio.run rejects a plain function, so the loop's call is stubbed out.
    monkeypatch.setattr("asyncio.run", lambda coro: coro)

    delays = _drive_run(part, stop_after=5)
    assert delays == [5.0, 10.0, 20.0, 40.0, 60.0]  # doubles, capped at 60s


def test_run_paces_a_server_side_close_instead_of_spinning(monkeypatch):
    """A clean disconnect used to reconnect with no delay at all."""
    part = _partition()
    monkeypatch.setattr(part, "seconds_until_open", lambda: 0.0)
    monkeypatch.setattr(part, "_consume", lambda: None, raising=False)
    monkeypatch.setattr("asyncio.run", lambda coro: coro)

    delays = _drive_run(part, stop_after=3)
    assert delays == [5.0, 10.0, 20.0]
    assert all(d > 0 for d in delays)


def test_unreachable_classification_survives_a_cyclic_cause():
    a = OSError("a")
    b = RuntimeError("b")
    a.__cause__ = b
    b.__cause__ = a
    assert _is_endpoint_unreachable(a) is False  # terminates rather than looping
