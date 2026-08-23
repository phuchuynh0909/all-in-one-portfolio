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

import orjson
import pytest

from infra.dnse_ws_input import (
    DnseTradePartition,
    build_auth_message,
    build_subscribe_message,
    compute_signature,
    normalize_side,
    trade_extra_channel,
    trade_extra_to_tick_payload,
    is_trade_frame,
    unwrap_frame,
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
