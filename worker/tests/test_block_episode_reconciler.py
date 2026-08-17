"""Offline tests for the block-episode reconciler.

No DNSE, no ClickHouse, no network — the DNSE and ClickHouse clients are
replaced by fakes. Covers the session/auction filtering, the detect wiring,
episode-key dedup, and the upsert (dry-run and real) paths.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import workers.block_episode_reconciler as r
from core.tick_contract import SIDE_BUY, SIDE_SELL
from model import BLOCK_EPISODES_COLUMNS


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeDNSEClient:
    """Returns a fixed list of raw (API-style) ticks for any symbol/day."""

    def __init__(self, raw_ticks):
        self._raw = raw_ticks
        self.calls = []

    def fetch_day_ticks(self, symbol, day, board):
        self.calls.append((symbol, day, board))
        return list(self._raw)


class FakeCHClient:
    """Stands in for ClickHouseClient: `.query()` and `.client.insert()`."""

    def __init__(self, key_rows=None):
        self._key_rows = key_rows or []
        self.queries = []
        self.inserts = []
        self.client = self  # so ch_client.client.insert(...) resolves here

    def query(self, sql):
        self.queries.append(sql)
        return SimpleNamespace(result_rows=self._key_rows)

    def insert(self, table, rows, column_names=None):
        self.inserts.append((table, rows, column_names))


def api_tick(iso, side, price, qty, symbol="FPT"):
    """Raw DNSE-style tick payload (what fetch_day_ticks yields)."""
    return {
        "symbol": symbol,
        "sendingTime": iso,
        "matchPrice": price,
        "matchQtty": qty,
        "side": "B" if side == SIDE_BUY else "S",
    }


# A Monday. Session (config default) is 09:00–15:00 ICT (+07:00) == 02:00–08:00 UTC.
DAY = "2026-06-22"


# ---------------------------------------------------------------------------
# Session / auction filtering
# ---------------------------------------------------------------------------
def test_fetch_session_ticks_filters_out_of_session_and_auctions():
    raw = [
        api_tick(f"{DAY}T08:30:00+07:00", SIDE_BUY, 100.0, 10),   # pre-open -> drop
        api_tick(f"{DAY}T09:05:00+07:00", SIDE_BUY, 100.0, 10),   # ATO auction -> drop
        api_tick(f"{DAY}T10:00:00+07:00", SIDE_BUY, 100.0, 10),   # in session -> keep
        api_tick(f"{DAY}T11:30:00+07:00", SIDE_SELL, 100.0, 5),   # in session -> keep
        api_tick(f"{DAY}T14:45:00+07:00", SIDE_BUY, 100.0, 10),   # ATC auction -> drop
        api_tick(f"{DAY}T15:30:00+07:00", SIDE_BUY, 100.0, 10),   # post-close -> drop
    ]
    client = FakeDNSEClient(raw)
    ticks = r.fetch_session_ticks("FPT", DAY, client)

    assert len(ticks) == 2
    hours_utc = sorted(t["sending_time"].astimezone(timezone.utc).hour for t in ticks)
    assert hours_utc == [3, 4]  # 10:00 and 11:30 ICT
    assert client.calls == [("FPT", date.fromisoformat(DAY), r.config.large_order.board)]


def test_fetch_session_ticks_normalizes_side_and_time_to_utc():
    raw = [api_tick(f"{DAY}T10:00:00+07:00", SIDE_SELL, 101.5, 7)]
    ticks = r.fetch_session_ticks("FPT", DAY, FakeDNSEClient(raw))
    assert len(ticks) == 1
    t = ticks[0]
    assert t["side"] == SIDE_SELL
    assert t["sending_time"].tzinfo is not None
    assert t["sending_time"].astimezone(timezone.utc).hour == 3


# ---------------------------------------------------------------------------
# detect_episodes wiring
# ---------------------------------------------------------------------------
def _sustained_buy_program_canonical(start="2026-06-22T02:30:00+00:00"):
    """Canonical ticks: a long quiet baseline then a sustained one-sided buy
    burst — enough prior bins to satisfy the default min_baseline_bins (300)."""
    base = datetime.fromisoformat(start)
    ticks = []
    t = 0.0
    for i in range(350):  # quiet alternating baseline (>= min_baseline_bins)
        side = SIDE_BUY if i % 2 == 0 else SIDE_SELL
        ticks.append(
            {
                "symbol": "FPT",
                "sending_time": base + timedelta(seconds=t),
                "match_price": 100.0,
                "match_qty": 1,
                "side": side,
            }
        )
        t += 1.0
    for _ in range(6):  # heavy, one-sided buy burst across consecutive seconds
        for k in range(4):
            ticks.append(
                {
                    "symbol": "FPT",
                    "sending_time": base + timedelta(seconds=t + k * 0.1),
                    "match_price": 100.0,
                    "match_qty": 500,
                    "side": SIDE_BUY,
                }
            )
        t += 1.0
    return ticks


def test_detect_episodes_finds_sustained_buy_program():
    episodes = r.detect_episodes(_sustained_buy_program_canonical())
    # There is exactly one *flow-cluster* episode — the sustained burst. (Under
    # the identical quiet notionals here, isolated large-print episodes also
    # appear, which is expected large-print behaviour and not what we assert on.)
    flow_eps = [
        e
        for e in episodes
        if e["candidate_type"] in ("FLOW_CLUSTER", "FLOW_CLUSTER_AND_LARGE_PRINT")
    ]
    assert len(flow_eps) == 1
    ep = flow_eps[0]
    assert ep["side"] == SIDE_BUY
    assert ep["num_bins"] >= 2
    assert ep["signed_notional"] > 0


def test_detect_episodes_empty_tape():
    assert r.detect_episodes([]) == []


# ---------------------------------------------------------------------------
# Episode keys + ClickHouse diff
# ---------------------------------------------------------------------------
def test_episode_key_is_symbol_start_side():
    start = datetime(2026, 6, 22, 3, 0, 0, tzinfo=timezone.utc)
    key = r._episode_key({"symbol": "FPT", "start_time": start, "side": SIDE_BUY})
    assert key == ("FPT", "2026-06-22T03:00:00.000000+00:00", SIDE_BUY)


def test_fetch_ch_episode_keys_reads_existing_rows():
    start = datetime(2026, 6, 22, 3, 0, 0, tzinfo=timezone.utc)
    ch = FakeCHClient(key_rows=[("FPT", start, SIDE_BUY)])
    keys = r.fetch_ch_episode_keys(ch, "default", "FPT", DAY)
    assert keys == {("FPT", "2026-06-22T03:00:00.000000+00:00", SIDE_BUY)}
    assert ch.queries and "block_episodes" in ch.queries[0]


# ---------------------------------------------------------------------------
# Upsert paths
# ---------------------------------------------------------------------------
def _episode(symbol="FPT", side=SIDE_BUY):
    start = datetime(2026, 6, 22, 3, 0, 0, tzinfo=timezone.utc)
    return {
        "symbol": symbol,
        "side": side,
        "start_time": start,
        "end_time": start + timedelta(seconds=5),
        "signed_notional": 1_000_000.0,
        "abs_notional": 1_000_000.0,
        "num_trades": 20,
        "num_bins": 5,
        "large_print_count": 1,
        "max_abs_z": 6.3,
        "max_abs_imbalance": 1.0,
        "candidate_type": "FLOW_CLUSTER_AND_LARGE_PRINT",
    }


def test_upsert_episodes_dry_run_does_not_insert():
    ch = FakeCHClient()
    upserted, failed = r.upsert_episodes(ch, "default", [_episode()], dry_run=True)
    assert (upserted, failed) == (1, 0)
    assert ch.inserts == []


def test_upsert_episodes_inserts_rows_with_correct_columns():
    ch = FakeCHClient()
    upserted, failed = r.upsert_episodes(ch, "default", [_episode()], dry_run=False)
    assert (upserted, failed) == (1, 0)
    assert len(ch.inserts) == 1
    table, rows, columns = ch.inserts[0]
    assert table == "default.block_episodes"
    assert columns == BLOCK_EPISODES_COLUMNS
    # One row, column-aligned to the schema.
    assert len(rows) == 1
    assert len(rows[0]) == len(BLOCK_EPISODES_COLUMNS)
    row = dict(zip(BLOCK_EPISODES_COLUMNS, rows[0]))
    assert row["symbol"] == "FPT"
    assert row["side"] == SIDE_BUY
    assert row["candidate_type"] == "FLOW_CLUSTER_AND_LARGE_PRINT"
    assert row["received_at"].tzinfo is not None


def test_upsert_episodes_empty_is_noop():
    ch = FakeCHClient()
    assert r.upsert_episodes(ch, "default", [], dry_run=False) == (0, 0)
    assert ch.inserts == []


def test_upsert_episodes_reports_failure(monkeypatch):
    ch = FakeCHClient()

    def boom(*a, **k):
        raise RuntimeError("clickhouse down")

    monkeypatch.setattr(ch, "insert", boom)
    upserted, failed = r.upsert_episodes(ch, "default", [_episode()], dry_run=False)
    assert (upserted, failed) == (0, 1)


# ---------------------------------------------------------------------------
# Range helper
# ---------------------------------------------------------------------------
def test_weekdays_in_range_skips_weekends():
    days = r.weekdays_in_range(date(2026, 6, 1), date(2026, 6, 7))  # Mon..Sun
    assert days == [date(2026, 6, d) for d in (1, 2, 3, 4, 5)]
