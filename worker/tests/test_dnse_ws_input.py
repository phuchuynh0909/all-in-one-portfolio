"""Offline tests for the DNSE OpenAPI Trade-Extra WebSocket source.

No network: the protocol is exercised through its pure helpers, the partition's
``_process_frame`` / ``next_batch`` (built with ``start=False`` so no socket
opens), and the ``_TradeExtraClient`` overrides driven against a fake
connection. Verifies channel/subscribe construction, the Trade-Extra ->
canonical-tick normalization (round-tripped through ``normalize_tick``),
ping/pong, and the credential guard.

Transport, HMAC auth and encoding now belong to the vendored SDK
(``worker/dnse_sdk``), so the tests here pin the *contracts this module depends
on* from that SDK — and, just as importantly, the four SDK behaviours
``_TradeExtraClient`` deliberately overrides.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import socket
import time
from datetime import datetime, timezone
from datetime import time as dtime
from zoneinfo import ZoneInfo

import orjson
import pytest

from infra.dnse_ws_input import (
    ALL_BOARDS,
    DEFAULT_BOARDS,
    DEFAULT_SESSION_TZ,
    DnseTradePartition,
    normalize_side,
    parse_hhmm,
    seconds_until_session,
    trade_extra_channel,
    trade_extra_to_tick_payload,
    is_trade_frame,
    unwrap_frame,
    _is_endpoint_unreachable,
    _ResilientDecoder,
    _time_to_iso,
    _TIME_REJECTS,
    _UNDECODABLE_ACTION,
)
from core.tick_contract import normalize_tick, SIDE_BUY, SIDE_SELL, SIDE_UNKNOWN

# Vendored at worker/dnse_sdk, so it imports off the same root as core/infra.
from dnse_sdk.dnse.websocket.auth import AuthManager
from dnse_sdk.dnse.websocket.models import TradeExtra, parse_timestamp


# ---------------------------------------------------------------------------
# HMAC auth — owned by the SDK's AuthManager since the refactor. These pin the
# framing we now rely on rather than re-testing deleted local code.
# ---------------------------------------------------------------------------
def test_sdk_auth_manager_signs_the_documented_message():
    signature = AuthManager("key", "secret").compute_signature(1_700_000_000, "12345")
    expected = hmac.new(
        b"secret", b"key:1700000000:12345", hashlib.sha256
    ).hexdigest()
    assert signature == expected


def test_sdk_auth_message_is_self_consistent():
    manager = AuthManager("mykey", "mysecret")
    msg = manager.create_auth_message()
    assert msg["action"] == "auth"
    assert msg["api_key"] == "mykey"
    assert msg["signature"] == manager.compute_signature(
        msg["timestamp"], msg["nonce"]
    )


# ---------------------------------------------------------------------------
# Channel / subscribe framing
# ---------------------------------------------------------------------------
def test_trade_extra_channel():
    assert trade_extra_channel("G1") == "tick_extra.G1.json"
    assert trade_extra_channel("T2", "msgpack") == "tick_extra.T2.msgpack"


def test_upstream_sends_exactly_one_channel_per_subscribe_frame():
    """Why the subscribe frames are not batched.

    Batching every board into one frame relies on `channels` accepting more
    than one entry — but no upstream path sends more than one, so nothing has
    exercised that against the gateway. A server reading only `channels[0]`
    would leave the rest silently unsubscribed, indistinguishable from boards
    with no trades. This asserts the assumption that made batching tempting is
    unsupported by the SDK itself.
    """
    import inspect
    from dnse_sdk.dnse.websocket.client import TradingClient

    source = inspect.getsource(TradingClient._subscribe_channel)
    assert '"channels": [{"name": channel' in source  # always a single entry


def test_the_default_subscription_is_the_board_that_actually_gets_stored():
    """G1 only. TICK_ALLOWED_BOARDS defaults to G1, so anything else arrived
    and was dropped before insert — paid for on the wire, never written."""
    assert DEFAULT_BOARDS == ["G1"]
    assert set(DEFAULT_BOARDS) <= set(ALL_BOARDS)


def test_config_and_module_board_defaults_agree():
    """These drifted once: config.py kept subscribing all nine while the module
    constant said G1, and config is the one production reads."""
    from config import DEFAULT_TRADE_BOARDS

    assert list(DEFAULT_TRADE_BOARDS) == DEFAULT_BOARDS


def test_config_board_default_is_what_from_env_yields(monkeypatch):
    """The constant is only meaningful if from_env actually falls back to it."""
    from config import DnseWsConfig

    monkeypatch.delenv("DNSE_TRADE_BOARDS", raising=False)
    assert DnseWsConfig.from_env().boards == DEFAULT_BOARDS
    monkeypatch.setenv("DNSE_TRADE_BOARDS", "G1,G4, T1 ")
    assert DnseWsConfig.from_env().boards == ["G1", "G4", "T1"]


# ---------------------------------------------------------------------------
# time normalization
# ---------------------------------------------------------------------------
def test_time_to_iso_variants():
    assert _time_to_iso(None) is None
    # protobuf {Seconds, Nanos}
    assert _time_to_iso({"Seconds": 1_781_000_000, "Nanos": 0}).endswith("+00:00")
    # unix seconds vs milliseconds resolve to the same instant
    secs = _time_to_iso(1_781_000_000)
    ms = _time_to_iso(1_781_000_000_000)
    assert secs == ms


def test_every_accepted_time_carries_an_explicit_utc_offset():
    """The invariant: nothing reaches normalize_tick without a zone on it."""
    for value in (
        "2026-06-22T03:00:00Z",
        "2026-06-22T10:00:00+07:00",
        {"Seconds": 1_781_000_000, "Nanos": 500_000_000},
        1_781_000_000,
        1_781_000_000_000,
    ):
        out = _time_to_iso(value)
        assert out is not None and out.endswith("+00:00"), value


def test_an_offset_string_is_converted_rather_than_passed_through():
    """ICT in, UTC out — the same instant, named in the column's own zone."""
    assert _time_to_iso("2026-06-22T10:00:00+07:00") == "2026-06-22T03:00:00+00:00"
    assert _time_to_iso("2026-06-22T03:00:00Z") == "2026-06-22T03:00:00+00:00"


def test_a_naive_string_is_dropped_instead_of_assumed_utc(caplog):
    """The bug this guards: normalize_tick reads a missing offset as UTC.

    Passing an ICT wall-clock string straight through would write every tick
    seven hours early, silently, with nothing downstream able to tell.
    """
    _TIME_REJECTS.clear()
    with caplog.at_level(logging.WARNING, logger="infra.dnse_ws_input"):
        assert _time_to_iso("2026-06-22T10:00:00") is None
    assert "no UTC offset" in caplog.text

    # And the tick really is dropped, not written under a guess.
    frame = dict(DOCS_TRADE_EXTRA_FRAME, time="2026-06-22T10:00:00")
    assert trade_extra_to_tick_payload(frame) is None
    part = _partition()
    part._process_frame(frame)
    assert part.next_batch() == []
    assert part._trades == 0  # not counted as delivered either


def test_an_unparseable_time_is_dropped(caplog):
    _TIME_REJECTS.clear()
    with caplog.at_level(logging.WARNING, logger="infra.dnse_ws_input"):
        assert _time_to_iso("not a timestamp") is None
    assert "Unparseable DNSE time" in caplog.text


def test_rejected_times_are_reported_at_a_widening_interval(caplog):
    """A changed feed shape hits this branch on every tick of the session."""
    _TIME_REJECTS.clear()
    with caplog.at_level(logging.WARNING, logger="infra.dnse_ws_input"):
        for _ in range(150):
            _time_to_iso("2026-06-22T10:00:00")
    # 1st and 100th, not 150 lines at several thousand ticks a second.
    assert caplog.text.count("no UTC offset") == 2
    assert _TIME_REJECTS["naive"] == 150


def test_protobuf_nanos_survive_to_microsecond_precision():
    """``seconds + nanos/1e9`` rounds before it reaches the us column.

    float64 resolves only ~240ns at a 1.8e9 epoch, so the sum lands a
    microsecond off — small, but the arithmetic is exact for free.
    """
    seconds, nanos = 1_779_766_822, 72_345_678
    assert _time_to_iso({"Seconds": seconds, "Nanos": nanos}) == (
        "2026-05-26T03:40:22.072345+00:00"
    )
    # What the old float path produced for the same input.
    rounded = datetime.fromtimestamp(seconds + nanos / 1e9, tz=timezone.utc)
    assert rounded.isoformat() == "2026-05-26T03:40:22.072346+00:00"


def test_sdk_parse_timestamp_keeps_the_utc_offset():
    """LOCAL PATCH guard. Upstream built every datetime with a bare
    ``fromtimestamp()`` and formatted with ``strftime``, dropping the offset:
    the protobuf and unix branches came out in the *host's* zone (right on a UTC
    container, seven hours off on an ICT laptop) and a string came out in
    whatever zone it arrived in — all three indistinguishable once returned.
    Re-vendoring the SDK reverts this."""
    for value in (
        "2017-08-03T00:00:00Z",
        "2017-08-03T07:00:00+07:00",
        {"Seconds": 1_501_718_400, "Nanos": 0},
        1_501_718_400,
        1_501_718_400_000,
    ):
        out = parse_timestamp(value)
        assert out == "2017-08-03T00:00:00+00:00", (value, out)


def test_sdk_parse_timestamp_and_time_to_iso_agree():
    """_time_to_iso delegates to it, so any drift means one of them regressed."""
    for value in (
        "2026-06-22T03:00:00Z",
        "2026-06-22T10:00:00+07:00",
        {"Seconds": 1_781_000_000, "Nanos": 500_000_000},
        1_781_000_000,
        1_781_000_000_000,
    ):
        assert parse_timestamp(value) == _time_to_iso(value), value


def test_sdk_parse_timestamp_refuses_to_guess_a_naive_timestamp():
    assert parse_timestamp("2017-08-03T00:00:00") is None
    # ...but a bare calendar date is a label, not an instant, and date_only
    # fields (finalTradeDate, listingDate) legitimately arrive without a zone.
    assert parse_timestamp("2027-08-03", date_only=True) == "2027-08-03"
    # An aware value under date_only yields the UTC date, not the local one.
    assert parse_timestamp("2027-08-03T23:00:00-05:00", date_only=True) == "2027-08-04"


def test_the_typed_model_now_carries_an_offset_too():
    """TradeExtra.time inherits the fix, so the model is no longer a trap."""
    assert TradeExtra.from_dict(DOCS_TRADE_EXTRA_FRAME).time == _time_to_iso(
        DOCS_TRADE_EXTRA_FRAME["time"]
    )


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
    """The reply is a dict, not encoded text: the SDK's encoder serialises it.

    Returning ``json.dumps(...)`` here would put a JSON string on the wire even
    on a msgpack-negotiated connection.
    """
    part = _partition()
    reply = part._process_frame({"action": "ping"})
    assert reply == {"action": "pong"}
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


# ---------------------------------------------------------------------------
# Board visibility
#
# boardId is dropped by trade_extra_to_tick_payload and the ticks table has no
# board column, so "only G1 arrives" cannot be checked anywhere downstream. The
# partition is the last place that sees it.
# ---------------------------------------------------------------------------
def _board_frame(board, symbol="FPT"):
    frame = dict(DOCS_TRADE_EXTRA_FRAME)
    frame["boardId"] = board
    frame["symbol"] = symbol
    return frame


def test_trades_are_tallied_per_board():
    part = _partition()
    for board in ("G1", "G1", "G4", "T1", "G1"):
        part._process_frame(_board_frame(board))
    assert part._board_counts == {"G1": 3, "G4": 1, "T1": 1}
    assert part._trades == 5


def test_board_split_is_logged_on_the_first_trade(caplog):
    """Immediate confirmation of which board is delivering, not after 1000."""
    part = _partition()
    with caplog.at_level(logging.INFO, logger="infra.dnse_ws_input"):
        part._process_frame(_board_frame("G1"))
    assert "by board after 1 trades" in caplog.text
    assert "G1=1" in caplog.text


def test_a_board_with_no_boardid_is_still_counted():
    part = _partition()
    frame = dict(DOCS_TRADE_EXTRA_FRAME)
    frame.pop("boardId")
    part._process_frame(frame)
    assert part._board_counts == {"?": 1}


def test_control_frames_are_logged_once_per_action(caplog):
    """A rejected channel is the answer to "why is this board silent"."""
    part = _partition()
    with caplog.at_level(logging.INFO, logger="infra.dnse_ws_input"):
        part._process_frame({"action": "subscribe_success", "channel": "tick_extra.G1.json"})
        part._process_frame({"action": "subscribe_success", "channel": "tick_extra.G4.json"})
    assert caplog.text.count("DNSE control frame") == 1  # deduped by action


def test_rejection_frames_are_logged_as_warnings(caplog):
    part = _partition()
    with caplog.at_level(logging.INFO, logger="infra.dnse_ws_input"):
        part._process_frame({"action": "subscribe_error", "message": "too many channels"})
    assert "too many channels" in caplog.text
    assert [r.levelno for r in caplog.records] == [logging.WARNING]


def test_control_frame_logging_is_truncated(caplog):
    part = _partition()
    with caplog.at_level(logging.INFO, logger="infra.dnse_ws_input"):
        part._process_frame({"action": "hello", "blob": "x" * 5000})
    assert len(caplog.text) < 1500
    assert "…" in caplog.text


def test_pings_and_trades_are_not_logged_as_control_frames(caplog):
    part = _partition()
    with caplog.at_level(logging.INFO, logger="infra.dnse_ws_input"):
        part._process_frame({"action": "ping"})
        part._process_frame(DOCS_TRADE_EXTRA_FRAME)
    assert "DNSE control frame" not in caplog.text


def test_msgpack_is_accepted_now_that_the_sdk_codec_decodes_it():
    """Was a hard refusal while frames went through json.loads."""
    part = DnseTradePartition("k", "s", ["FPT"], encoding="msgpack", start=False)
    assert part._encoding == "msgpack"
    assert trade_extra_channel("G1", part._encoding) == "tick_extra.G1.msgpack"


def test_an_unknown_encoding_is_refused_rather_than_silently_empty():
    """An unnegotiable codec subscribes fine and then discards every frame."""
    with pytest.raises(RuntimeError, match="not supported"):
        DnseTradePartition("k", "s", ["FPT"], encoding="protobuf", start=False)


def test_partition_and_source_agree_on_the_default_encoding():
    part = _partition()
    assert part._encoding == "json"


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


# ---------------------------------------------------------------------------
# SDK overrides
#
# Transport, auth, encoding and the heartbeat come from TradingClient. These
# pin the four behaviours _TradeExtraClient deliberately does NOT inherit —
# each of which is a silent failure if it ever regresses to the SDK default.
# ---------------------------------------------------------------------------
class _FakeConnection:
    """Stands in for the SDK's WebSocketConnection; records what was sent."""

    def __init__(self):
        self.sent = []
        self.is_connected = True  # a property on the real connection

    async def send(self, message):
        self.sent.append(message)


def _client(part=None):
    """An authenticated client wired to a fake socket, no I/O performed."""
    part = part or _partition()
    client = part._build_client()
    client._connection = _FakeConnection()
    client._is_authenticated = True
    return part, client


def _sent_frames(client):
    return [json.loads(raw) for raw in client._connection.sent]


def test_sdk_reconnect_machinery_is_disabled_in_favour_of_the_session_gate():
    """TradingClient would dial straight through a closed exchange.

    Its WebSocketConnection also retries a failed connect ten times with its
    own 1..60s backoff — about eight minutes on a hostname that, out of
    session, does not resolve — before _run's clock check gets a look in.
    """
    _, client = _client()
    assert client.auto_reconnect is False
    assert client.max_retries == 1


def test_subscribe_sends_one_sdk_frame_per_configured_board():
    """The SDK's own path, driven by our board list."""
    part = _partition(boards=["G1", "G4", "T1"])
    part, client = _client(part)
    asyncio.run(client.subscribe_trade_extra_boards(part._boards, part._symbols))

    frames = _sent_frames(client)
    assert len(frames) == 3
    assert [f["channels"][0]["name"] for f in frames] == [
        "tick_extra.G1.json",
        "tick_extra.G4.json",
        "tick_extra.T1.json",
    ]
    assert all(len(f["channels"]) == 1 for f in frames)
    assert all(f["channels"][0]["symbols"] == ["FPT", "HPG"] for f in frames)
    # Recorded where the SDK's reconnect path — and _infer_msg_type — look.
    assert set(client._subscriptions) == {
        "tick_extra.G1.json",
        "tick_extra.G4.json",
        "tick_extra.T1.json",
    }


def test_subscribe_uses_our_board_list_not_the_sdks_nine_board_default():
    """subscribe_trade_extra(board_id=None) means the SDK's nine boards, so the
    loop has to be on our side or a G1-only config silently subscribes to all."""
    part, client = _client(_partition(boards=["G1"]))
    asyncio.run(client.subscribe_trade_extra_boards(part._boards, part._symbols))
    assert set(client._subscriptions) == {"tick_extra.G1.json"}


def test_subscribe_honours_the_negotiated_encoding():
    part = DnseTradePartition("k", "s", ["FPT"], encoding="msgpack", start=False)
    part, client = _client(part)
    asyncio.run(client.subscribe_trade_extra_boards(["G1"], ["FPT"]))
    assert set(client._subscriptions) == {"tick_extra.G1.msgpack"}


def test_subscribe_before_auth_is_refused_like_the_sdk_does():
    part, client = _client()
    client._is_authenticated = False
    with pytest.raises(Exception, match="authenticate"):
        asyncio.run(client.subscribe_trade_extra_boards(part._boards, part._symbols))


# ---------------------------------------------------------------------------
# Vendored-SDK patch
#
# worker/dnse_sdk is a local copy of upstream, and these pin a fix applied
# inside it: upstream routed market data solely on data["T"] and its if/elif
# chain had no else, so a frame without that tag was discarded in silence.
# Re-vendoring the SDK reverts the fix, and these are what will say so.
# ---------------------------------------------------------------------------
def _sdk_client(subscriptions=("tick_extra.G1.json",)):
    from dnse_sdk.dnse.websocket.client import TradingClient

    client = TradingClient("k", "s")
    client._subscriptions = {name: {"symbols": [], "kwargs": {}} for name in subscriptions}
    return client


def test_sdk_recovers_the_message_type_when_the_frame_has_no_T():
    """The core of the patch: tick_extra must route without a T tag."""
    assert "T" not in DOCS_TRADE_EXTRA_FRAME
    assert _sdk_client()._infer_msg_type(DOCS_TRADE_EXTRA_FRAME) == "te"


def test_sdk_uses_the_subscription_set_to_separate_trade_from_trade_extra():
    """Same payload shape; only what was subscribed can tell them apart."""
    assert _sdk_client(("tick.G1.json",))._infer_msg_type(DOCS_TRADE_EXTRA_FRAME) == "t"
    assert _sdk_client(("tick_extra.G1.json",))._infer_msg_type(
        DOCS_TRADE_EXTRA_FRAME
    ) == "te"


def test_sdk_falls_back_to_side_when_subscriptions_cannot_decide():
    """Subscribed to both: only trade_extra carries the aggressor side."""
    both = _sdk_client(("tick.G1.json", "tick_extra.G1.json"))
    assert both._infer_msg_type(DOCS_TRADE_EXTRA_FRAME) == "te"
    no_side = {k: v for k, v in DOCS_TRADE_EXTRA_FRAME.items() if k != "side"}
    assert both._infer_msg_type(no_side) == "t"


def test_sdk_routes_other_channels_by_shape_too():
    """The bug hit every channel, so the fix is not trade-extra-specific."""
    client = _sdk_client()
    quote = {"symbol": "HPG", "bid": [{"price": 24.3}], "boardId": "G1", "time": 1}
    assert client._infer_msg_type(quote) == "q"
    foreign = {"symbol": "HPG", "foreignInvestorTypeCode": "01"}
    assert client._infer_msg_type(foreign) == "f"


def test_sdk_declines_to_guess_an_unknown_shape():
    """Guessing would hand from_dict a payload it fills with None."""
    assert _sdk_client()._infer_msg_type({"something": "unfamiliar"}) is None


def test_sdk_logs_a_frame_it_cannot_route_instead_of_dropping_it(caplog):
    """The missing else. Silence here is what made a dead feed look healthy."""
    client = _sdk_client()
    with caplog.at_level(logging.WARNING, logger="dnse_sdk.dnse.websocket.client"):
        asyncio.run(client._dispatch_message({"something": "unfamiliar"}))
        asyncio.run(client._dispatch_message({"something": "unfamiliar"}))
    assert caplog.text.count("Unrouted frame") == 1  # once per distinct shape


def test_dispatch_does_not_gate_on_the_T_marker():
    """The whole reason for the override.

    TradingClient routes market data through _MSG_TYPE_MAP on data["T"]. The
    documented Trade-Extra payload has no "T", so inheriting that dispatch
    drops the entire feed while every log line still says "subscribed".
    """
    part, client = _client()
    assert "T" not in DOCS_TRADE_EXTRA_FRAME
    asyncio.run(client._dispatch_message(dict(DOCS_TRADE_EXTRA_FRAME)))

    batch = part.next_batch()
    assert len(batch) == 1
    assert normalize_tick(orjson.loads(batch[0][1]))["symbol"] == "41I1G6000"


def test_dispatch_answers_ping_through_the_negotiated_encoder():
    part, client = _client()
    asyncio.run(client._dispatch_message({"action": "ping"}))
    assert _sent_frames(client) == [{"action": "pong"}]


def test_dispatch_records_pong_without_logging_it(caplog):
    part, client = _client()
    client._last_pong_time = 0.0
    with caplog.at_level(logging.INFO, logger="infra.dnse_ws_input"):
        asyncio.run(client._dispatch_message({"action": "pong"}))
    assert client._last_pong_time > 0.0  # keeps is_healthy meaningful
    assert "DNSE control frame" not in caplog.text
    assert part.next_batch() == []


def test_receivedat_injected_by_the_sdk_does_not_break_trade_detection():
    """_message_handler stamps every frame before dispatch."""
    part, client = _client()
    frame = dict(DOCS_TRADE_EXTRA_FRAME, _receivedAt=1_781_000_000.5)
    asyncio.run(client._dispatch_message(frame))
    assert len(part.next_batch()) == 1


def test_an_undecodable_frame_is_counted_instead_of_killing_the_session(caplog):
    """The SDK decodes inline and treats EncodingError as fatal.

    With auto_reconnect off that means one malformed frame ends an otherwise
    healthy session, so the decoder is swapped for one that reports instead.
    """
    part = _partition()
    decoder = _ResilientDecoder("json", part._count_undecodable)
    with caplog.at_level(logging.WARNING, logger="infra.dnse_ws_input"):
        assert decoder.decode(b"\xff\xfe not json") == {"action": _UNDECODABLE_ACTION}
    assert part._undecodable == 1
    assert "undecodable frame(s)" in caplog.text
    # A good frame still decodes normally.
    assert decoder.decode(b'{"action": "pong"}') == {"action": "pong"}
    assert part._undecodable == 1


def test_undecodable_frames_are_skipped_by_dispatch(caplog):
    part, client = _client()
    with caplog.at_level(logging.INFO, logger="infra.dnse_ws_input"):
        asyncio.run(client._dispatch_message({"action": _UNDECODABLE_ACTION}))
    assert part.next_batch() == []
    assert "DNSE control frame" not in caplog.text  # counted once, not per frame


def test_msgpack_client_encodes_its_replies_as_msgpack():
    """The end-to-end point of enabling msgpack: replies use it too."""
    import msgpack

    part = DnseTradePartition("k", "s", ["FPT"], encoding="msgpack", start=False)
    part, client = _client(part)
    asyncio.run(client._dispatch_message({"action": "ping"}))
    assert msgpack.unpackb(client._connection.sent[0], raw=False) == {"action": "pong"}


def test_a_gateway_that_never_pongs_is_not_treated_as_stalled():
    """The reconnect-storm guard, and the reason is_healthy isn't used raw.

    DNSE documents the *server* pinging us every three minutes; nothing says it
    answers our heartbeat. If it never does, is_healthy reads false 50s into
    every connection and the reconnect loop never lets a session live.
    """
    _, client = _client()
    client._connection.is_connected = True
    client._last_pong_time = time.time() - 10_000  # far past 2x the heartbeat
    assert client.is_healthy is False  # what the SDK would tell us
    assert client.is_stalled() is False  # ...and why we don't ask it that way


def test_a_gateway_that_stops_ponging_is_treated_as_stalled():
    part, client = _client()
    client._connection.is_connected = True
    asyncio.run(client._dispatch_message({"action": "pong"}))
    assert client._pong_seen and client.is_stalled() is False

    # Now it goes quiet while the socket stays open.
    client._last_pong_time = time.time() - 10_000
    assert client.is_stalled() is True


def test_a_dropped_or_unauthenticated_connection_is_stalled():
    _, client = _client()
    client._connection.is_connected = False
    assert client.is_stalled() is True

    _, client = _client()
    client._connection.is_connected = True
    client._is_authenticated = False
    assert client.is_stalled() is True


def test_consume_returns_when_the_connection_stalls(caplog, monkeypatch):
    """An open socket delivering nothing reads as a quiet market otherwise."""
    monkeypatch.setattr("infra.dnse_ws_input._HEALTH_CHECK_SECS", 0.05)
    part = _partition()
    stalled = {"yet": False}

    async def _drive():
        import websockets

        original = websockets.connect
        sock = _FakeSocket(
            _json_frames({"action": "welcome"}, {"action": "auth_success"})
        )

        async def _fake_connect(url, **_kwargs):
            return sock

        websockets.connect = _fake_connect
        try:
            client_box = {}
            real_build = part._build_client

            def _capture():
                client = real_build()
                client.is_stalled = lambda: stalled["yet"]
                client_box["c"] = client
                return client

            part._build_client = _capture
            task = asyncio.create_task(part._consume())
            # Healthy first: the loop must not exit on its own.
            await asyncio.sleep(0.1)
            assert not task.done()
            stalled["yet"] = True
            await asyncio.wait_for(task, timeout=30)
            return sock
        finally:
            websockets.connect = original

    with caplog.at_level(logging.WARNING, logger="infra.dnse_ws_input"):
        sock = asyncio.run(_drive())
    assert "up but stalled" in caplog.text
    assert sock.closed  # disconnect() ran, so _run rebuilds a clean session


def test_sdk_stream_handlers_are_detached_so_logs_are_not_duplicated():
    """The SDK attaches its own StreamHandler at import and leaves propagate on."""
    for name in ("dnse_sdk.dnse.websocket.client", "dnse_sdk.dnse.websocket.connection"):
        assert logging.getLogger(name).handlers == []


# ---------------------------------------------------------------------------
# _consume end-to-end, still offline
#
# Everything above tests one seam. This drives the real path — the SDK's
# connect / welcome / HMAC handshake, its receive loop and per-symbol dispatch
# workers, our subscribe frame and our dispatch — against a fake socket, which
# is the only place the wiring between the two is actually exercised.
# ---------------------------------------------------------------------------
class _FakeSocket:
    """Minimal stand-in for a websockets client connection.

    ``recv`` blocks once the scripted frames run out rather than closing, so the
    partition's own stop flag is what ends the session — the same shape as a
    quiet but healthy market.
    """

    def __init__(self, frames):
        self._incoming = asyncio.Queue()
        for frame in frames:
            self._incoming.put_nowait(frame)
        self.sent = []
        self.closed = False

    async def send(self, message):
        self.sent.append(message)

    async def recv(self):
        return await self._incoming.get()

    async def close(self):
        self.closed = True


def _run_consume(part, frames, timeout=5.0):
    """Run ``part._consume()`` until a tick arrives, then stop it."""
    sockets = []

    async def _fake_connect(url, **_kwargs):
        sockets.append(_FakeSocket(frames))
        return sockets[-1]

    async def _drive():
        import websockets

        original = websockets.connect
        websockets.connect = _fake_connect
        try:
            task = asyncio.create_task(part._consume())
            deadline = asyncio.get_running_loop().time() + timeout
            while not part._q.qsize() and not task.done():
                if asyncio.get_running_loop().time() > deadline:
                    break
                await asyncio.sleep(0.01)
            part._stop.set()
            await asyncio.wait_for(task, timeout=timeout)
        finally:
            websockets.connect = original

    asyncio.run(_drive())
    return sockets[0]


def _json_frames(*payloads):
    return [json.dumps(p).encode() for p in payloads]


def test_consume_handshakes_subscribes_and_delivers_a_trade():
    part = _partition(boards=["G1", "G4"])
    sock = _run_consume(
        part,
        _json_frames(
            {"action": "welcome", "session_id": "s-1"},
            {"action": "auth_success"},
            DOCS_TRADE_EXTRA_FRAME,
        ),
    )

    sent = [json.loads(raw) for raw in sock.sent]
    assert sent[0]["action"] == "auth"
    assert sent[0]["api_key"] == "key"
    assert "signature" in sent[0] and sent[0]["signature"] != "secret"
    # One subscribe frame per board, each with a single channel — the SDK's
    # own framing, driven from our board list.
    subscribes = [f for f in sent if f.get("action") == "subscribe"]
    assert [f["channels"][0]["name"] for f in subscribes] == [
        "tick_extra.G1.json",
        "tick_extra.G4.json",
    ]
    assert all(len(f["channels"]) == 1 for f in subscribes)
    assert subscribes[0]["channels"][0]["symbols"] == ["FPT", "HPG"]

    batch = part.next_batch()
    assert len(batch) == 1
    tick = normalize_tick(orjson.loads(batch[0][1]))
    assert tick["symbol"] == "41I1G6000" and tick["match_price"] == 2022.5
    assert sock.closed  # disconnect() ran in the finally


def test_consume_raises_on_a_rejected_auth_so_run_can_back_off():
    """A bad secret must surface as an error, not a silent no-trade session."""
    part = _partition()
    sockets = []
    with pytest.raises(Exception, match="Authentication failed"):
        sockets.append(
            _run_consume(
                part,
                _json_frames(
                    {"action": "welcome"},
                    {"action": "auth_error", "message": "invalid signature"},
                ),
            )
        )
    assert not _is_endpoint_unreachable(
        RuntimeError("Authentication failed")
    )  # so _run logs it as a warning, not a routine blip


def test_a_rejected_auth_still_closes_the_socket():
    """auth failure raises after the socket is up; _run then retries forever.

    Without the close, a wrong DNSE_API_SECRET leaks one open connection every
    5-60s for as long as the worker runs.
    """
    opened = []

    async def _fake_connect(url, **_kwargs):
        opened.append(
            _FakeSocket(
                _json_frames(
                    {"action": "welcome"},
                    {"action": "auth_error", "message": "invalid signature"},
                )
            )
        )
        return opened[-1]

    async def _drive():
        import websockets

        original = websockets.connect
        websockets.connect = _fake_connect
        try:
            with pytest.raises(Exception, match="Authentication failed"):
                await _partition()._consume()
        finally:
            websockets.connect = original

    asyncio.run(_drive())
    assert len(opened) == 1 and opened[0].closed
