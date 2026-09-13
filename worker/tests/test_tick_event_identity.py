"""Stable DNSE event identity and reconciliation behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.tick_contract import normalize_tick
from model import TICKS_CLICKHOUSE_ORDER_BY, TICKS_CREATE_TABLE_DDL
from workers import reconciler
from workers.reconciler import diff_ticks

NOW = datetime(2026, 9, 11, 2, 15, tzinfo=timezone.utc)


def _row(sequence: int, *, qty: int = 10, price: float = 40.7, side: int = 2):
    return {
        "symbol": "BCM",
        "sending_time": NOW,
        "match_price": price,
        "match_qty": qty,
        "side": side,
        "received_at": NOW,
        "board_id": "G1",
        "event_sequence": sequence,
    }


def test_identical_prints_with_different_sequences_both_survive_diff():
    source = [_row(100), _row(110)]
    missing, drift = diff_ticks(source, [_row(100)])
    assert [row["event_sequence"] for row in missing] == [110]
    assert drift == []


def test_same_sequence_in_different_auction_times_is_distinct():
    opening = _row(100)
    closing = _row(100)
    closing["sending_time"] = NOW + timedelta(hours=5)
    missing, drift = diff_ticks([opening, closing], [])
    assert missing == [opening, closing]
    assert drift == []


def test_same_event_identity_with_changed_values_is_drift():
    missing, drift = diff_ticks([_row(100, qty=20)], [_row(100, qty=10)])
    assert missing == []
    assert [(row["event_sequence"], row["match_qty"]) for row in drift] == [
        (100, 20)
    ]


def test_exact_source_replay_is_deduplicated():
    row = _row(100)
    missing, drift = diff_ticks([row, dict(row)], [])
    assert missing == [row]
    assert drift == []


def test_source_replay_prefers_known_side():
    unknown = _row(100, side=0)
    valid = _row(100, side=2)
    missing, drift = diff_ticks([unknown, valid], [])
    assert missing == [valid]
    assert drift == []


def test_conflicting_source_identity_is_rejected():
    with pytest.raises(ValueError, match="conflicting event identity"):
        diff_ticks([_row(100, qty=10), _row(100, qty=20)], [])


def test_normalizer_requires_and_carries_dnse_sequence():
    raw = {
        "symbol": "BCM",
        "sendingTime": "2026-09-11T02:15:00Z",
        "matchPrice": 40.7,
        "matchQtty": 10,
        "side": 2,
        "boardId": "G1",
        "totalVolumeTraded": 110,
    }
    tick = normalize_tick(raw)
    assert tick is not None and tick["event_sequence"] == 110
    del raw["totalVolumeTraded"]
    assert normalize_tick(raw) is None


def test_replacing_key_uses_event_sequence_not_trade_values():
    ddl = TICKS_CREATE_TABLE_DDL.format(database="db", table="ticks")
    assert "event_sequence UInt64" in ddl
    assert f"ORDER BY ({TICKS_CLICKHOUSE_ORDER_BY})" in ddl
    order_by = ddl.split("ORDER BY (", 1)[1].split(")\n", 1)[0]
    for mutable_value in ("match_price", "match_qty", "side", "received_at"):
        assert mutable_value not in order_by


def test_empty_authoritative_response_preserves_existing_rows(monkeypatch):
    client = object()
    monkeypatch.setattr(reconciler, "get_clickhouse_client", lambda: client)
    monkeypatch.setattr(reconciler, "_assert_event_identity_schema", lambda *_args: None)
    monkeypatch.setattr(reconciler, "fetch_session_ticks", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        reconciler,
        "fetch_ch_session_ticks",
        lambda **_kwargs: [_row(100)],
    )

    def must_not_mutate(*_args, **_kwargs):
        raise AssertionError("empty upstream response must not mutate ClickHouse")

    monkeypatch.setattr(reconciler, "patch_ticks", must_not_mutate)
    monkeypatch.setattr(reconciler, "_delete_legacy_rows", must_not_mutate)

    metrics = reconciler.run_reconciler("2026-09-11", symbol="BCM")
    assert metrics.fetched_rows == 0
    assert metrics.failed_rows == 1
