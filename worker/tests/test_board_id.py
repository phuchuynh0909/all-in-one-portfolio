"""Tests for the ``board_id`` column and the ingest-side board filter.

Boards decide whether a printed price belongs in a bar at all: "G1" is the main
continuous order book, "G4"/"G7" are odd lot, and "T1".."T6" are put-through —
negotiated off-book, which is why ``backend/app/services/dnse_client.py``
excludes them when picking a quote. Before this column the pipeline dropped
``boardId`` at the source, so a put-through print landed in ``ticks``
indistinguishable from a continuous-market trade.
"""

from __future__ import annotations

import datetime as dt

import pyarrow as pa
import pytest

from core.tick_contract import (
    BOARD_UNKNOWN,
    normalize_board,
    normalize_tick,
    to_clickhouse_tuple,
)
from infra.clickhouse_sink import rows_to_arrow
from infra.dnse_ws_input import trade_extra_to_tick_payload
from model import TICKS_ADD_BOARD_ID_DDL, TICKS_ARROW_SCHEMA


def _api_tick(board=None, **over):
    raw = {
        "symbol": "FPT",
        "sendingTime": "2026-08-25T03:00:00Z",
        "matchPrice": 100.5,
        "matchQtty": 300,
        "side": "B",
        **over,
    }
    if board is not None:
        raw["boardId"] = board
    return raw


# ---------------------------------------------------------------------------
# Spelling normalization
#
# The OpenAPI WebSocket sends "G1"; the legacy stream and the mock source send
# "BOARD_ID_G1". Two spellings in one LowCardinality column would make
# `board_id = 'G1'` quietly miss half the rows.
# ---------------------------------------------------------------------------
def test_normalize_board_strips_the_legacy_prefix():
    assert normalize_board("BOARD_ID_G1") == "G1"
    assert normalize_board("G1") == "G1"
    assert normalize_board("board_id_t3") == "T3"
    assert normalize_board(" g4 ") == "G4"


def test_normalize_board_treats_absence_as_unknown():
    assert normalize_board(None) == BOARD_UNKNOWN == ""


def test_both_feed_spellings_normalize_to_one_value():
    ws = normalize_tick(_api_tick(board="G1"))
    legacy = normalize_tick(_api_tick(board="BOARD_ID_G1"))
    assert ws["board_id"] == legacy["board_id"] == "G1"


# ---------------------------------------------------------------------------
# The column reaches ClickHouse
# ---------------------------------------------------------------------------
def test_normalize_tick_carries_board_id():
    assert normalize_tick(_api_tick(board="T1"))["board_id"] == "T1"


def test_pre_existing_payloads_without_a_board_are_unknown_not_g1():
    """Rows from a nine-board subscription must not be relabelled as G1."""
    assert normalize_tick(_api_tick())["board_id"] == BOARD_UNKNOWN


def test_clickhouse_tuple_puts_board_id_last():
    tick = normalize_tick(_api_tick(board="G1"))
    row = to_clickhouse_tuple(tick)
    assert len(row) == len(TICKS_ARROW_SCHEMA) == 7
    assert row[-1] == "G1"
    assert [f.name for f in TICKS_ARROW_SCHEMA][-1] == "board_id"


def test_clickhouse_tuple_tolerates_a_dict_from_before_the_column():
    """A canonical dict built by older code still converts."""
    tick = normalize_tick(_api_tick(board="G1"))
    del tick["board_id"]
    assert to_clickhouse_tuple(tick)[-1] == BOARD_UNKNOWN


def test_rows_with_board_id_build_an_arrow_block():
    rows = [to_clickhouse_tuple(normalize_tick(_api_tick(board=b))) for b in ("G1", "T3")]
    table = rows_to_arrow(rows, TICKS_ARROW_SCHEMA)
    assert table.schema == TICKS_ARROW_SCHEMA
    assert table.column("board_id").to_pylist() == ["G1", "T3"]


def test_websocket_payload_passes_board_through():
    frame = {
        "symbol": "FPT",
        "boardId": "G4",
        "matchPrice": 2022.5,
        "matchQtty": 1.0,
        "side": "SELL",
        "time": {"Seconds": 1779766822, "Nanos": 0},
    }
    payload = trade_extra_to_tick_payload(frame)
    assert payload["boardId"] == "G4"
    # ...and survives the trip through the shared normalizer.
    assert normalize_tick(payload)["board_id"] == "G4"


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------
def test_add_column_ddl_is_idempotent_and_defaults_to_unknown():
    sql = TICKS_ADD_BOARD_ID_DDL.format(database="db", table="ticks")
    assert "ADD COLUMN IF NOT EXISTS board_id" in sql
    assert "DEFAULT ''" in sql  # existing rows read back as unknown, not "G1"
    assert "ALTER TABLE db.ticks" in sql


# ---------------------------------------------------------------------------
# Ingest filter
# ---------------------------------------------------------------------------
def _filter(board, allowed):
    """The board test as tick_ingest.key_by_symbol_ingest applies it."""
    normalized = normalize_tick(_api_tick(board=board))
    value = normalized["board_id"]
    return not (allowed and value and value not in allowed)


def test_only_the_allowed_board_is_stored():
    allowed = frozenset({"G1"})
    assert _filter("G1", allowed) is True
    assert _filter("BOARD_ID_G1", allowed) is True  # spelling-independent
    assert _filter("T3", allowed) is False  # put-through: negotiated off-book
    assert _filter("G4", allowed) is False


def test_a_wider_allow_list_admits_the_odd_lot_books():
    allowed = frozenset({"G1", "G7", "G4"})
    assert all(_filter(b, allowed) for b in ("G1", "G7", "G4"))
    assert not _filter("T6", allowed)


def test_an_empty_allow_list_stores_every_board():
    assert all(_filter(b, frozenset()) for b in ("G1", "G4", "T3", "T6"))


def test_an_unknown_board_is_kept_rather_than_dropped():
    """Dropping these would silently discard any feed that omits boardId."""
    assert _filter(None, frozenset({"G1"})) is True


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def test_allowed_boards_defaults_to_the_continuous_book(monkeypatch):
    monkeypatch.delenv("TICK_ALLOWED_BOARDS", raising=False)
    from config import TickSyncConfig

    assert TickSyncConfig.from_env().allowed_boards == frozenset({"G1"})


def test_allowed_boards_is_configurable(monkeypatch):
    from config import TickSyncConfig

    monkeypatch.setenv("TICK_ALLOWED_BOARDS", " g1 , G7 ,")
    assert TickSyncConfig.from_env().allowed_boards == frozenset({"G1", "G7"})
    monkeypatch.setenv("TICK_ALLOWED_BOARDS", "")
    assert TickSyncConfig.from_env().allowed_boards == frozenset()
