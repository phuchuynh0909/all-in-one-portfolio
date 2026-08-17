"""Offline tests for the block-episode live ingest dataflow wiring.

The ClickHouse sink (``bytewax.clickhouse.operators.output``) connects to the
live cluster when the flow is built, so it is stubbed *before* importing the
worker. No MQTT or ClickHouse connection is made — only the pure wiring
(``key_by_symbol`` parsing/filtering and the ``detect_step`` stateful mapper)
is exercised. The detection maths itself is covered by test_large_execution.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone

import orjson
import pytest

# --- Stub the ClickHouse sink operator so building `flow` makes no connection.
_fake_ch = types.ModuleType("bytewax.clickhouse")
_fake_ops = types.ModuleType("bytewax.clickhouse.operators")
_fake_ops.output = lambda *a, **k: None  # no-op sink
_fake_ch.operators = _fake_ops
sys.modules.setdefault("bytewax.clickhouse", _fake_ch)
sys.modules.setdefault("bytewax.clickhouse.operators", _fake_ops)

import workers.block_episode_ingest as w  # noqa: E402
from core.large_execution import SymbolDetector  # noqa: E402
from core.tick_contract import SIDE_BUY  # noqa: E402

BASE = datetime(2026, 6, 22, 3, 0, 0, tzinfo=timezone.utc)  # in-session (10:00 ICT)


def _mqtt(symbol, offset_s, side, price=100.0, qty=10):
    payload = orjson.dumps(
        {
            "symbol": symbol,
            "sendingTime": (BASE + timedelta(seconds=offset_s)).isoformat(),
            "matchPrice": price,
            "matchQtty": qty,
            "side": "B" if side == SIDE_BUY else "S",
        }
    )
    return ("some/topic", payload)


def test_flow_object_exists():
    assert w.flow is not None
    assert w.DETECTION_PARAMS.z_threshold == w.config.block_episode.z_threshold


def test_key_by_symbol_accepts_watchlist_symbol():
    symbol = next(iter(w.WATCHLIST_SET))
    key, tick = w.key_by_symbol(_mqtt(symbol, 0, SIDE_BUY))
    assert key == symbol
    assert tick is not None
    assert tick["symbol"] == symbol
    assert tick["side"] == SIDE_BUY


def test_key_by_symbol_drops_off_watchlist():
    key, tick = w.key_by_symbol(_mqtt("__NOT_A_SYMBOL__", 0, SIDE_BUY))
    assert tick is None


def test_key_by_symbol_drops_malformed_payload():
    key, tick = w.key_by_symbol(("t", b"not-json"))
    assert tick is None


def test_detect_step_initializes_state_and_returns_list():
    symbol = next(iter(w.WATCHLIST_SET))
    _key, tick = w.key_by_symbol(_mqtt(symbol, 0, SIDE_BUY))
    state, episodes = w.detect_step(None, tick)
    assert isinstance(state, SymbolDetector)
    assert state.symbol == symbol
    assert isinstance(episodes, list)  # 0 episodes from a single opening tick


def test_detect_step_reuses_state_across_ticks():
    symbol = next(iter(w.WATCHLIST_SET))
    _k, t0 = w.key_by_symbol(_mqtt(symbol, 0, SIDE_BUY))
    state, _ = w.detect_step(None, t0)
    _k, t1 = w.key_by_symbol(_mqtt(symbol, 1, SIDE_BUY))
    state2, episodes = w.detect_step(state, t1)
    assert state2 is state  # same detector object threaded through
    assert isinstance(episodes, list)
