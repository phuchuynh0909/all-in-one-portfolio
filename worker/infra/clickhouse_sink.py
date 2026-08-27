"""ClickHouse sink for Bytewax with tunable ingestion settings.

``bytewax.clickhouse`` cannot express ClickHouse's two ingestion levers:

* its ``write_batch`` hardcodes ``settings={"buffer_size": 0}``, so
  ``async_insert`` can never be passed — and this deployment's user lacks
  ``ALTER USER``, so it cannot be set as a user default either;
* its ``output`` operator batches with ``op.collect``, which is **keyed** — a
  stream keyed by symbol therefore produces one small insert *per symbol* per
  flush (196 of them here) rather than one block.

This module fixes both: every row is re-keyed onto a single batching key so a
flush becomes one insert, and the insert settings come from
``config.IngestTuningConfig``.

Batching is this module's own ``StatefulLogic`` rather than ``op.collect``,
because ``op.collect``'s timeout is not the latency bound this pipeline needs:
its ``on_item`` re-arms ``timeout_at`` on *every* item, so the timer measures
the gap between items. Keyed per symbol that was survivable — each symbol goes
quiet for two seconds often enough — but the re-key above funnels 196 symbols
onto one key, and that stream has no two-second gap while the market is open.
The timer therefore never fires and a block is only cut at ``max_size``: a live
tape at ~20 trades/s held rows for 51 seconds at ``max_size=1000``, and would
hold them for over an hour at the documented default of 100,000.
``batch_builder`` arms the deadline once, at the first row of a block, so the
timeout means "no row waits longer than this" — which is what
``INGEST_BATCH_TIMEOUT_SECONDS`` is documented to promise.

Table creation is deliberately absent — callers own their DDL (see
``workers.tick_ingest.ensure_ticks_table``), because the upstream sink's
auto-create silently produces a table with no PARTITION BY, no codecs and no
``ReplacingMergeTree`` version column.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import bytewax.operators as op
import pyarrow as pa  # type: ignore
from bytewax.dataflow import Stream, operator
from bytewax.operators import StatefulLogic
from bytewax.outputs import DynamicSink, StatelessSinkPartition
from clickhouse_connect import get_client  # type: ignore
from typing_extensions import override

log = logging.getLogger(__name__)

# All rows funnel onto this key so `op.collect` batches globally instead of
# per symbol. The value is arbitrary — it never reaches ClickHouse.
COALESCE_KEY = "_ch_batch"


@dataclass
class _BatchState:
    """Rows accumulated so far, and when the block must be cut regardless."""

    acc: List[Any] = field(default_factory=list)
    deadline: Optional[datetime] = None


class _BoundedBatchLogic(StatefulLogic):
    """Collect rows into a block, bounded by both a size and a *latency*.

    The one difference from ``bytewax.operators.collect`` — and the reason this
    exists — is that ``deadline`` is set when a block opens and then left alone,
    so it expires while rows keep arriving. See the module docstring.
    """

    def __init__(
        self,
        timeout: timedelta,
        max_size: int,
        now_getter: Callable[[], datetime],
        state: _BatchState,
    ):
        self._timeout = timeout
        self._max_size = max_size
        self._now = now_getter
        self.state = state

    @override
    def on_item(self, value: Any) -> Tuple[Iterable[List[Any]], bool]:
        if not self.state.acc:
            # First row of a new block: start the clock. Later rows must not
            # push it out, or a busy stream never flushes on time.
            self.state.deadline = self._now() + self._timeout
        self.state.acc.append(value)
        if len(self.state.acc) >= self._max_size:
            # Safe to hand out the list itself: DISCARD drops this state.
            return ((self.state.acc,), StatefulLogic.DISCARD)
        return ((), StatefulLogic.RETAIN)

    @override
    def on_notify(self) -> Tuple[Iterable[List[Any]], bool]:
        return self._drain()

    @override
    def on_eof(self) -> Tuple[Iterable[List[Any]], bool]:
        return self._drain()

    @override
    def notify_at(self) -> Optional[datetime]:
        return self.state.deadline

    @override
    def snapshot(self) -> _BatchState:
        return copy.deepcopy(self.state)

    def _drain(self) -> Tuple[Iterable[List[Any]], bool]:
        # An empty block would reach rows_to_arrow as a zero-column table, so
        # nothing is emitted for it; the logic is discarded either way.
        if not self.state.acc:
            return ((), StatefulLogic.DISCARD)
        return ((self.state.acc,), StatefulLogic.DISCARD)


def batch_builder(
    timeout: timedelta,
    max_size: int,
    now_getter: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Callable[[Optional[_BatchState]], _BoundedBatchLogic]:
    """The ``op.stateful`` builder for latency-bounded batching.

    :arg now_getter: injectable clock — the tests drive it by hand so a
        latency bound can be asserted without sleeping through it.
    """

    def build(resume_state: Optional[_BatchState]) -> _BoundedBatchLogic:
        state = resume_state if resume_state is not None else _BatchState()
        return _BoundedBatchLogic(timeout, max_size, now_getter, state)

    return build


@operator
def collect_bounded(
    step_id: str,
    up: Stream,
    timeout: timedelta,
    max_size: int,
) -> Stream:
    """``op.collect`` with a latency bound instead of an idle-gap bound.

    Drop-in for ``op.collect(step_id, up, timeout, max_size)``: a keyed stream
    of items in, a keyed stream of lists out.
    """
    return op.stateful("stateful", up, batch_builder(timeout, max_size))


def rows_to_arrow(rows: List[Tuple], pa_schema: pa.Schema) -> pa.Table:
    """Build one Arrow table from a list of positional row tuples."""
    columns = list(zip(*rows))
    arrays = [pa.array(columns[i], field.type) for i, field in enumerate(pa_schema)]
    return pa.Table.from_arrays(arrays, schema=pa_schema)


class _TunedClickHousePartition(StatelessSinkPartition):
    """Inserts Arrow blocks with caller-supplied ClickHouse settings."""

    def __init__(
        self,
        table_name: str,
        database: str,
        host: str,
        port: int,
        username: str,
        password: str,
        secure: bool,
        settings: Dict[str, Any],
    ):
        self._target = f"{database}.{table_name}"
        self._settings = settings
        self._rows = 0
        self._inserts = 0
        self.client = get_client(
            host=host,
            port=port,
            username=username,
            password=password,
            database=database,
            secure=secure,
        )

    @override
    def write_batch(self, batch: List[pa.Table]) -> None:
        if not batch:
            return
        table = batch[0] if len(batch) == 1 else pa.concat_tables(batch)
        if table.num_rows == 0:
            return
        self.client.insert_arrow(self._target, table, settings=self._settings)
        self._rows += table.num_rows
        self._inserts += 1
        # The first insert is logged as well as every hundredth: on a quiet tape
        # a 100-insert threshold alone can take an hour to trip, which is
        # indistinguishable from a sink that is not writing at all.
        if self._inserts == 1 or self._inserts % 100 == 0:
            log.info(
                "%s: %d inserts, %d rows (avg %.0f rows/insert)",
                self._target, self._inserts, self._rows, self._rows / self._inserts,
            )

    @override
    def close(self) -> None:
        if self._inserts:
            log.info(
                "%s: closing after %d inserts, %d rows (avg %.0f rows/insert)",
                self._target, self._inserts, self._rows, self._rows / self._inserts,
            )
        self.client.close()


class TunedClickHouseSink(DynamicSink):
    """Bytewax sink writing Arrow blocks to ClickHouse. One client per worker."""

    def __init__(
        self,
        table_name: str,
        database: str,
        host: str,
        port: int,
        username: str,
        password: str,
        secure: bool = False,
        settings: Optional[Dict[str, Any]] = None,
    ):
        self._table_name = table_name
        self._database = database
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._secure = secure
        self._settings = settings or {"buffer_size": 0}

    def build(self, _step_id: str, _worker_index: int, _worker_count: int):
        return _TunedClickHousePartition(
            self._table_name,
            self._database,
            self._host,
            self._port,
            self._username,
            self._password,
            self._secure,
            self._settings,
        )


@operator
def output(
    step_id: str,
    up: Stream,
    pa_schema: pa.Schema,
    table_name: str,
    database: str,
    host: str,
    port: int,
    username: str,
    password: str,
    secure: bool = False,
    timeout: timedelta = timedelta(seconds=2),
    max_size: int = 100_000,
    settings: Optional[Dict[str, Any]] = None,
) -> None:
    """Batch a keyed stream of row tuples and insert it into ClickHouse.

    :arg up: keyed stream of ``(key, row_tuple)`` — the key is used only for
        routing and is replaced by a single batching key, so one flush is one
        insert regardless of how many symbols it spans.

    :arg timeout: the longest a row may wait before it is inserted. This is
        the practical lever: ``max_size`` is rarely reached, so this bounds
        insert latency (and ``async_insert`` does the coalescing). Measured
        from the first row of each block — see the module docstring for why
        that is this module's code and not ``op.collect``'s.

    :arg max_size: flush as soon as this many rows accumulate.

    :arg settings: clickhouse-connect INSERT settings, e.g.
        ``config.ingest.insert_settings()``.
    """
    keyed = op.map("coalesce", up, lambda kv: (COALESCE_KEY, kv[1]))
    collected = collect_bounded("batch", keyed, timeout=timeout, max_size=max_size)
    tables = op.map("to_arrow", collected, lambda kv: rows_to_arrow(kv[1], pa_schema))
    return op.output(
        "insert",
        tables,
        TunedClickHouseSink(
            table_name,
            database,
            host,
            port,
            username,
            password,
            secure=secure,
            settings=settings,
        ),
    )
