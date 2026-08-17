"""Block-episode ("large-execution footprint") live ingest — Bytewax dataflow.

Live counterpart to ``block_episode_reconciler``. Subscribes to the tick feed
for every watchlist symbol, drops ATO/ATC auction prints, and runs the
incremental :class:`core.large_execution.SymbolDetector` per symbol (a keyed
``stateful_map``). Each time a candidate 1-second bin closes, the growing block
episode is re-emitted and upserted into the ClickHouse ``block_episodes`` table.

The streaming detector reproduces the batch pipeline exactly (see the
equivalence tests): a bin closes when a later-second trade arrives (event-time),
so a quiet symbol's final bin only closes once more ticks arrive — the daily
reconciler remains authoritative and overwrites partial live episodes
(ReplacingMergeTree keyed by ``(symbol, start_time, side)``).

Run:
    python -m bytewax.run workers.block_episode_ingest:flow
"""

from datetime import datetime, timedelta, timezone

import orjson
from bytewax.dataflow import Dataflow
import bytewax.operators as op
from bytewax.clickhouse import operators as ch_operators

from infra.dnse_ws_input import DnseTradeSource
from infra.mock_clickhouse import MockClickHouseSource
from config import config
from core.tick_contract import normalize_tick
from core.large_order import is_auction_time
from core.large_execution import SymbolDetector, to_episode_row
from core.watchlist import load_symbols
from model import (
    BLOCK_EPISODES_ARROW_SCHEMA,
    BLOCK_EPISODES_CLICKHOUSE_SCHEMA,
    BLOCK_EPISODES_CLICKHOUSE_TABLE,
    BLOCK_EPISODES_CLICKHOUSE_ORDER_BY,
)

WATCHLIST_SYMBOLS = load_symbols(config.large_order.watchlist_file)
WATCHLIST_SET = set(WATCHLIST_SYMBOLS)
SESSION_TZ = config.large_order.session_tz
AUCTION_WINDOWS = config.large_order.auction_windows
DETECTION_PARAMS = config.block_episode.detection_params


def key_by_symbol(item):
    """Parse an MQTT message; key the normalized tick by symbol.

    Maps (topic, payload) -> (symbol, tick). Returns (symbol, None) for
    malformed payloads or off-watchlist symbols so the filter can drop them.
    """
    _topic, payload = item
    try:
        raw = orjson.loads(payload)
    except Exception:
        return ("", None)

    raw_symbol = raw.get("symbol", "")
    if raw_symbol not in WATCHLIST_SET:
        return (raw_symbol, None)

    tick = normalize_tick(raw)
    if tick is None:
        return (raw_symbol, None)

    return (tick["symbol"], tick)


def detect_step(state, tick):
    """Keyed stateful_map: feed the tick to this symbol's detector.

    ``state`` is the per-symbol ``SymbolDetector`` (None on first tick). Returns
    (state, [episode, ...]) — the episode snapshots to upsert (usually 0 or 1).
    """
    if state is None:
        state = SymbolDetector(DETECTION_PARAMS, symbol=tick["symbol"])
    episodes = state.push(tick)
    return (state, episodes)


# ---------- Build dataflow ----------
flow = Dataflow("block_episode_ingest")

if config.mock.enabled:
    print("Using MockClickHouseSource for block_episode_ingest")
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
        f"Using DnseTradeSource (WebSocket) for block_episode_ingest — "
        f"{len(WATCHLIST_SYMBOLS)} symbols, z>={DETECTION_PARAMS.z_threshold}, "
        f"bin={DETECTION_PARAMS.bin_seconds}s"
    )
    if not WATCHLIST_SYMBOLS:
        raise SystemExit(
            "block_episode_ingest: watchlist is empty — check "
            f"{config.large_order.watchlist_file}"
        )
    # Credentials are validated by DnseTradePartition when the source is built
    # at run time (a clear RuntimeError), so the module still imports offline.
    stream = op.input(
        "dnse_trades",
        flow,
        DnseTradeSource(
            config.dnse_ws.api_key,
            config.dnse_ws.api_secret,
            WATCHLIST_SYMBOLS,
            boards=config.dnse_ws.boards,
            base_url=config.dnse_ws.base_url,
            encoding=config.dnse_ws.encoding,
        ),
    )

# 1) Parse, normalize, key by symbol.
keyed = op.map("key_by_symbol", stream, key_by_symbol)

# 2) Drop malformed / off-watchlist ticks.
valid = op.filter("filter_valid", keyed, lambda item: item[1] is not None)

# 2b) Drop ATO/ATC auction prints (single-price clearings, not real flow).
non_auction = op.filter(
    "drop_auctions",
    valid,
    lambda item: not is_auction_time(item[1]["sending_time"], SESSION_TZ, AUCTION_WINDOWS),
)

# 3) Per-symbol incremental detection -> (symbol, [episode, ...]).
detected = op.stateful_map("detect", non_auction, detect_step)

# 4) Fan out each emitted episode snapshot into its own (symbol, episode) item.
episodes = op.flat_map(
    "expand_episodes",
    detected,
    lambda kv: [(kv[0], ep) for ep in kv[1]],
)

# 5) Convert to a ClickHouse insertion tuple, stamped with received_at.
transformed = op.map(
    "to_episode_row",
    episodes,
    lambda kv: (kv[0], to_episode_row(kv[1], datetime.now(timezone.utc))),
)

# 6) Sink to ClickHouse block_episodes table.
ch_operators.output(
    "block_episodes",
    transformed,
    pa_schema=BLOCK_EPISODES_ARROW_SCHEMA,
    table_name=BLOCK_EPISODES_CLICKHOUSE_TABLE,
    ch_schema=BLOCK_EPISODES_CLICKHOUSE_SCHEMA,
    order_by=BLOCK_EPISODES_CLICKHOUSE_ORDER_BY,
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
