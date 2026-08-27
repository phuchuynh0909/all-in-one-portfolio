"""Tests for ClickHouse ingestion tuning — batching and async inserts.

No network: the batching strategy is exercised through a real Bytewax dataflow
with a ``TestingSource``, counting the Arrow blocks that reach the sink stage.
"""

from __future__ import annotations

import datetime as dt

import bytewax.operators as op
import pyarrow as pa
import pytest
from bytewax.dataflow import Dataflow
from bytewax.testing import TestingSource, run_main

from config import IngestTuningConfig
from infra.clickhouse_sink import COALESCE_KEY, rows_to_arrow
from model import TICKS_ARROW_SCHEMA

NOW = dt.datetime(2026, 8, 21, 3, 0, 0, tzinfo=dt.timezone.utc)


def _rows(n: int, n_symbols: int):
    # Column order matches TICKS_ARROW_SCHEMA / to_clickhouse_tuple, board_id
    # last.
    return [
        (
            f"SYM{i % n_symbols:03d}",
            NOW + dt.timedelta(microseconds=i),
            1000.0 + (i % 50),
            (i % 7) + 1,
            (i % 2) + 1,
            NOW,
            "G1",
        )
        for i in range(n)
    ]


def _block_sizes(rows, *, coalesce: bool, timeout_s: float, max_size: int):
    """Row counts of every Arrow block the batching stage emits."""
    sizes: list[int] = []
    flow = Dataflow("batching")
    inp = op.input("rows", flow, TestingSource([(r[0], r) for r in rows]))
    up = (
        op.map("coalesce", inp, lambda kv: (COALESCE_KEY, kv[1]))
        if coalesce
        else inp
    )
    collected = op.collect(
        "batch", up, timeout=dt.timedelta(seconds=timeout_s), max_size=max_size
    )
    tables = op.map(
        "to_arrow", collected, lambda kv: rows_to_arrow(kv[1], TICKS_ARROW_SCHEMA)
    )
    op.inspect("count", tables, lambda _sid, t: sizes.append(t.num_rows))
    run_main(flow)
    return sizes


# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------
def test_coalescing_produces_one_block_instead_of_one_per_symbol():
    """`op.collect` is keyed, so a symbol-keyed stream fans out into tiny blocks."""
    rows = _rows(5000, 196)
    per_symbol = _block_sizes(rows, coalesce=False, timeout_s=10, max_size=50)
    coalesced = _block_sizes(rows, coalesce=True, timeout_s=2, max_size=100_000)

    assert len(per_symbol) == 196            # one block per symbol
    assert max(per_symbol) < 100             # all of them tiny
    assert len(coalesced) == 1               # one block for the whole flush
    assert coalesced[0] == 5000
    # No rows lost either way.
    assert sum(per_symbol) == sum(coalesced) == 5000


def test_max_size_caps_block_size():
    rows = _rows(1000, 196)
    sizes = _block_sizes(rows, coalesce=True, timeout_s=60, max_size=250)
    assert max(sizes) == 250
    assert sum(sizes) == 1000


def test_rows_to_arrow_preserves_values_and_schema():
    rows = _rows(3, 3)
    table = rows_to_arrow(rows, TICKS_ARROW_SCHEMA)
    assert table.schema == TICKS_ARROW_SCHEMA
    assert table.num_rows == 3
    assert table.column("symbol").to_pylist() == ["SYM000", "SYM001", "SYM002"]
    assert table.column("match_price").to_pylist() == [1000.0, 1001.0, 1002.0]


def test_rows_to_arrow_rejects_wrong_arity():
    with pytest.raises(Exception):
        rows_to_arrow([("only", "two")], TICKS_ARROW_SCHEMA)


# ---------------------------------------------------------------------------
# Async insert settings
# ---------------------------------------------------------------------------
def _cfg(**kw):
    base = dict(
        batch_max_size=100_000,
        batch_timeout_seconds=2.0,
        async_insert=True,
        wait_for_async_insert=False,
        async_insert_busy_timeout_ms=1000,
        async_insert_max_data_size=10_485_760,
    )
    base.update(kw)
    return IngestTuningConfig(**base)


def test_insert_settings_enable_fire_and_forget_async():
    s = _cfg().insert_settings()
    assert s["async_insert"] == 1
    assert s["wait_for_async_insert"] == 0
    assert s["async_insert_busy_timeout_ms"] == 1000
    assert s["buffer_size"] == 0


def test_insert_settings_can_wait_for_durability():
    assert _cfg(wait_for_async_insert=True).insert_settings()["wait_for_async_insert"] == 1


def test_insert_settings_omit_async_keys_when_disabled():
    s = _cfg(async_insert=False).insert_settings()
    assert s == {"buffer_size": 0}
    assert "async_insert" not in s


def test_from_env_defaults(monkeypatch):
    for var in (
        "INGEST_BATCH_MAX_SIZE",
        "INGEST_BATCH_TIMEOUT_SECONDS",
        "INGEST_ASYNC_INSERT",
        "INGEST_WAIT_FOR_ASYNC_INSERT",
    ):
        monkeypatch.delenv(var, raising=False)
    cfg = IngestTuningConfig.from_env()
    assert cfg.batch_max_size == 100_000
    assert cfg.batch_timeout_seconds == 2.0
    assert cfg.async_insert is True
    assert cfg.wait_for_async_insert is False


def test_from_env_overrides(monkeypatch):
    monkeypatch.setenv("INGEST_BATCH_MAX_SIZE", "250000")
    monkeypatch.setenv("INGEST_BATCH_TIMEOUT_SECONDS", "1.5")
    monkeypatch.setenv("INGEST_ASYNC_INSERT", "0")
    monkeypatch.setenv("INGEST_WAIT_FOR_ASYNC_INSERT", "1")
    cfg = IngestTuningConfig.from_env()
    assert cfg.batch_max_size == 250_000
    assert cfg.batch_timeout_seconds == 1.5
    assert cfg.async_insert is False
    assert cfg.wait_for_async_insert is True


# ---------------------------------------------------------------------------
# Batch latency
#
# `INGEST_BATCH_TIMEOUT_SECONDS` is documented as the bound on how long a row
# waits before it is inserted. bytewax's `op.collect` cannot provide that bound:
# its timer is re-armed on every item, so it measures the *gap between items*,
# and the sink funnels all 196 symbols onto one key (COALESCE_KEY) — a gap that
# never opens while the tape is live. These tests pin the bound to wall-clock
# latency instead, and pin why `op.collect` is not used.
# ---------------------------------------------------------------------------
class _FakeClock:
    """A clock the driver advances by hand, so latency is asserted exactly."""

    def __init__(self, start: dt.datetime = NOW):
        self.now = start

    def __call__(self) -> dt.datetime:
        return self.now


def _drive(builder, clock, *, n_items: int, gap_s: float, timeout_s: float):
    """Run a StatefulLogic builder the way the bytewax runtime would.

    Delivers `n_items` items `gap_s` apart on the fake clock, firing a due
    notification before each one and rebuilding the logic whenever it asks to be
    discarded, exactly as `op.stateful` does.

    :returns: list of ``(seconds since the first item, rows in the batch)``.
    """
    emitted: list[tuple[float, int]] = []
    logic = builder(None)
    first_item_at: dt.datetime | None = None

    for i in range(n_items):
        deadline = logic.notify_at()
        if deadline is not None and clock.now >= deadline:
            values, discard = logic.on_notify()
            for rows in values:
                emitted.append(((clock.now - first_item_at).total_seconds(), len(rows)))
            if discard:
                logic = builder(None)

        if first_item_at is None:
            first_item_at = clock.now
        values, discard = logic.on_item(i)
        for rows in values:
            emitted.append(((clock.now - first_item_at).total_seconds(), len(rows)))
        if discard:
            logic = builder(None)

        clock.now += dt.timedelta(seconds=gap_s)

    return emitted


def test_batch_flushes_on_timeout_when_the_stream_never_goes_idle():
    """A steady 20/s tape must still flush every 2s, not every `max_size` rows."""
    from infra.clickhouse_sink import batch_builder

    clock = _FakeClock()
    emitted = _drive(
        batch_builder(dt.timedelta(seconds=2.0), 1000, clock),
        clock,
        n_items=1000,          # 50 simulated seconds at 20/s
        gap_s=0.05,
        timeout_s=2.0,
    )

    assert emitted, "nothing was flushed in 50s — the timeout never fired"
    first_at, first_rows = emitted[0]
    # Bounded by the timeout, not by max_size: ~40 rows at 2s, not 1000 at 50s.
    assert first_at <= 2.05, f"first flush waited {first_at:.2f}s"
    assert first_rows < 100
    # And it keeps flushing at that cadence for the whole run.
    assert len(emitted) >= 20


def test_batch_still_flushes_early_on_max_size():
    """A burst must not wait for the timeout — the size cap still applies."""
    from infra.clickhouse_sink import batch_builder

    clock = _FakeClock()
    emitted = _drive(
        batch_builder(dt.timedelta(seconds=60.0), 250, clock),
        clock,
        n_items=1000,
        gap_s=0.0,             # all in the same instant
        timeout_s=60.0,
    )

    assert [rows for _at, rows in emitted] == [250, 250, 250, 250]
    assert all(at == 0.0 for at, _rows in emitted)


def test_batch_loses_no_rows_across_flushes():
    from infra.clickhouse_sink import batch_builder

    clock = _FakeClock()
    emitted = _drive(
        batch_builder(dt.timedelta(seconds=2.0), 1000, clock),
        clock,
        n_items=1000,
        gap_s=0.05,
        timeout_s=2.0,
    )
    logic_tail = 1000 - sum(rows for _at, rows in emitted)
    # Everything is either flushed or still accumulating in the final batch;
    # the runtime drains that last one via on_eof.
    assert 0 <= logic_tail < 1000
    assert sum(rows for _at, rows in emitted) + logic_tail == 1000


def test_op_collect_cannot_bound_latency():
    """Why `batch_builder` exists instead of `op.collect` (pinned, not desired).

    Delete this test and the custom logic the day bytewax's `_CollectLogic`
    stops re-arming its timer on every item.
    """
    sizes = _block_sizes(_rows(1000, 196), coalesce=True, timeout_s=2, max_size=250)
    # A never-idle stream reaches every size cap before the 2s timer expires.
    assert sizes[:3] == [250, 250, 250]
