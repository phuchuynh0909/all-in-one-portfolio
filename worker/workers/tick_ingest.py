"""Tick ingestion — Bytewax streaming dataflow.

Consumes the DNSE OpenAPI Trade-Extra WebSocket feed
(``wss://ws-openapi.dnse.com.vn/v1/stream``, HMAC-SHA256 auth) for every
watchlist symbol plus the current VN30F front-month contract, and bulk-inserts
canonical ticks into the ClickHouse ``ticks`` table.

Boards ``G1`` (even lot) and ``G4`` (odd lot) carry both stocks and
derivatives, so one subscription covers equities and the futures contract.

Run:
    python -m bytewax.run workers.tick_ingest:flow
"""

import orjson
from datetime import timedelta
from bytewax.dataflow import Dataflow
import bytewax.operators as op
from bytewax.clickhouse import operators as ch_operators

from infra.dnse_ws_input import DnseTradeSource
from infra.mock_clickhouse import MockClickHouseSource
from infra.clickhouse_client import ClickHouseClient
from config import config
from core.tick_contract import normalize_tick, to_clickhouse_tuple
from core.vn30f_symbol import current_symbol as vn30f_current_symbol
from core.watchlist import load_symbols
from model import (
    TICKS_ARROW_SCHEMA,
    TICKS_CLICKHOUSE_SCHEMA,
    TICKS_CLICKHOUSE_TABLE,
    TICKS_CLICKHOUSE_ORDER_BY,
    TICKS_CREATE_TABLE_DDL,
)


def _ingest_symbols() -> list[str]:
    """Every symbol this worker ingests: watchlist + the VN30F front month.

    De-duplicated, order-preserved (watchlist first, futures symbol last).
    """
    live_symbol = config.tick_sync.symbol or vn30f_current_symbol()
    symbols: list[str] = []
    seen: set[str] = set()
    for sym in [*load_symbols(), live_symbol]:
        if sym and sym not in seen:
            seen.add(sym)
            symbols.append(sym)
    return symbols


INGEST_SYMBOLS = _ingest_symbols()
# In mock mode ticks are replayed from ClickHouse for config.mock.symbols, so
# accept those instead of the live watchlist.
ALLOWED_SYMBOLS = (
    set(config.mock.symbols) if config.mock.enabled else set(INGEST_SYMBOLS)
)


def key_by_symbol_ingest(item):
    """Parse a source message and normalize tick data.

    Maps (topic, payload) → (symbol, normalized_dict).
    Returns (symbol, None) for malformed or filtered ticks.

    Source-agnostic: ``DnseTradeSource`` reshapes Trade-Extra frames into the
    same API-style payload the MQTT feed emitted, so ``normalize_tick`` parses
    either. The symbol filter is kept even though the WebSocket feed already
    filters server-side — boards G1/G4 are shared, so unrelated symbols can
    still arrive.
    """
    _topic, payload = item
    try:
        raw = orjson.loads(payload)
    except Exception:
        return ("", None)

    raw_symbol = raw.get("symbol", "")
    if ALLOWED_SYMBOLS and raw_symbol not in ALLOWED_SYMBOLS:
        return (raw_symbol, None)

    normalized = normalize_tick(raw)
    if normalized is None:
        return (raw_symbol, None)

    return (normalized["symbol"], normalized)


def transform_for_ticks(item):
    """Convert normalized tick to ClickHouse insertion tuple.

    Maps (symbol, tick_dict) → (symbol, clickhouse_tuple).
    """
    symbol, tick_dict = item
    return (symbol, to_clickhouse_tuple(tick_dict))


def ensure_ticks_table() -> None:
    """Create `ticks` with the canonical DDL before the sink can auto-create it.

    ``bytewax.clickhouse`` only auto-creates a bare
    ``ReplacingMergeTree() ORDER BY tuple(...)`` — no PARTITION BY and no
    version column. On a fresh database that silently costs us partition
    pruning (every query scans the whole table), cheap per-month
    ``DROP/REPLACE PARTITION``, and de-duplication on restart.
    ``TICKS_CREATE_TABLE_DDL`` is ``IF NOT EXISTS``, so this is a no-op
    against an already-correct table.
    """
    ClickHouseClient().query(
        TICKS_CREATE_TABLE_DDL.format(
            database=config.clickhouse.database, table=TICKS_CLICKHOUSE_TABLE
        )
    )


# ---------- Build dataflow ----------
# Must run before ch_operators.output below, which creates the table itself
# (with the wrong engine settings) if it is still missing.
ensure_ticks_table()

flow = Dataflow("tick_ingest")

# 1) Ingest from the DNSE WebSocket or Mock source based on configuration
if config.mock.enabled:
    print("Using MockClickHouseSource for tick_ingest")
    stream = op.input(
        "mock_ticks",
        flow,
        MockClickHouseSource(
            config.clickhouse.host,
            config.clickhouse.port,
            config.clickhouse.user,
            config.clickhouse.password,
            config.clickhouse.database,
            config.mock.symbols,
            config.mock.start_time,
            config.mock.end_time,
            config.mock.speed,
            config.mock.loop,
            "mock/ticks",
        ),
    )
else:
    print(
        f"Using DnseTradeSource (WebSocket) for tick_ingest — "
        f"{len(INGEST_SYMBOLS)} symbols "
        f"(watchlist + {config.tick_sync.symbol or vn30f_current_symbol()}), "
        f"boards={','.join(config.dnse_ws.boards)}"
    )
    if not INGEST_SYMBOLS:
        raise SystemExit("tick_ingest: no symbols to subscribe — check watchlist.json")
    # Credentials are validated by DnseTradePartition when the source is built
    # at run time (a clear RuntimeError), so the module still imports offline.
    stream = op.input(
        "dnse_trades",
        flow,
        DnseTradeSource(
            config.dnse_ws.api_key,
            config.dnse_ws.api_secret,
            INGEST_SYMBOLS,
            boards=config.dnse_ws.boards,
            base_url=config.dnse_ws.base_url,
            encoding=config.dnse_ws.encoding,
        ),
    )

# 2) Parse, normalize, and key by symbol
keyed = op.map("key_by_symbol_ingest", stream, key_by_symbol_ingest)

# 3) Drop malformed / non-matching ticks
filtered = op.filter("filter_valid", keyed, lambda item: item[1] is not None)

# 4) Transform for ClickHouse insertion
transformed = op.map("transform_for_ticks", filtered, transform_for_ticks)

# 5) Sink to ClickHouse ticks table
ch_operators.output(
    "ticks",
    transformed,
    pa_schema=TICKS_ARROW_SCHEMA,
    table_name=TICKS_CLICKHOUSE_TABLE,
    ch_schema=TICKS_CLICKHOUSE_SCHEMA,
    order_by=TICKS_CLICKHOUSE_ORDER_BY,
    database=config.clickhouse.database,
    host=config.clickhouse.host,
    port=config.clickhouse.port,
    username=config.clickhouse.user,
    password=config.clickhouse.password,
    timeout=timedelta(seconds=10),
)

if __name__ == "__main__":
    from bytewax.execution import run_main

    run_main(flow)
