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

Table creation is deliberately absent — callers own their DDL (see
``workers.tick_ingest.ensure_ticks_table``), because the upstream sink's
auto-create silently produces a table with no PARTITION BY, no codecs and no
``ReplacingMergeTree`` version column.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

import bytewax.operators as op
import pyarrow as pa  # type: ignore
from bytewax.dataflow import Stream, operator
from bytewax.outputs import DynamicSink, StatelessSinkPartition
from clickhouse_connect import get_client  # type: ignore
from typing_extensions import override

log = logging.getLogger(__name__)

# All rows funnel onto this key so `op.collect` batches globally instead of
# per symbol. The value is arbitrary — it never reaches ClickHouse.
COALESCE_KEY = "_ch_batch"


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
        if self._inserts % 100 == 0:
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

    :arg timeout: flush a partial block after this long. The practical lever
        for a low-rate tape: ``max_size`` is rarely reached, so this bounds
        insert latency (and ``async_insert`` does the coalescing).

    :arg max_size: flush as soon as this many rows accumulate.

    :arg settings: clickhouse-connect INSERT settings, e.g.
        ``config.ingest.insert_settings()``.
    """
    keyed = op.map("coalesce", up, lambda kv: (COALESCE_KEY, kv[1]))
    collected = op.collect("batch", keyed, timeout=timeout, max_size=max_size)
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
