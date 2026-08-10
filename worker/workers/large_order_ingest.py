"""Large-order ("Layer 3") block ingest — Bytewax streaming dataflow.

Subscribes to the live tick feed for *every* symbol in the watchlist, then
merges trades into fixed-second blocks per (symbol, side) using an event-time
tumbling window. Only blocks whose total notional value
(sum of price*qty) clears ``LARGE_ORDER_MIN_VALUE`` (default 1000) are
bulk-inserted into the ClickHouse `large_orders` table.

A single institutional order usually arrives as many sub-second fills, so
block-merging captures the true size that per-tick filtering would miss.

Note: with event-time windows a block flushes only once the watermark passes
the bucket end (driven by later trades + ``LARGE_ORDER_WAIT_SECONDS``). Quiet
symbols' final blocks may lag until more ticks arrive; the daily reconciler
back-fills the authoritative end-of-day blocks regardless.

Run:
    python -m bytewax.run workers.large_order_ingest:flow
"""

import orjson
from datetime import datetime, timedelta, timezone
from bytewax.dataflow import Dataflow
import bytewax.operators as op
from bytewax.operators.windowing import EventClock, TumblingWindower, fold_window
from bytewax.clickhouse import operators as ch_operators

from infra.mqtt_input import MqttSource
from infra.mock_clickhouse import MockClickHouseSource
from config import config
from core.tick_contract import normalize_tick
from core.large_order import (
    BLOCK_ALIGN_EPOCH,
    new_block_acc,
    fold_tick,
    merge_acc,
    finalize_block,
    is_large_block,
    is_auction_time,
    to_block_tuple,
)
from core.watchlist import load_symbols
from model import (
    LARGE_ORDERS_ARROW_SCHEMA,
    LARGE_ORDERS_CLICKHOUSE_SCHEMA,
    LARGE_ORDERS_CLICKHOUSE_TABLE,
    LARGE_ORDERS_CLICKHOUSE_ORDER_BY,
)

TICK_TOPIC_TEMPLATE = "plaintext/quotes/krx/mdds/tick/v1/roundlot/symbol/{symbol}"

WATCHLIST_SYMBOLS = load_symbols(config.large_order.watchlist_file)
WATCHLIST_SET = set(WATCHLIST_SYMBOLS)
MIN_VALUE = config.large_order.min_dollar_value
WINDOW_SECONDS = config.large_order.window_seconds
WAIT_SECONDS = config.large_order.wait_seconds
SESSION_TZ = config.large_order.session_tz
AUCTION_WINDOWS = config.large_order.auction_windows


def key_by_symbol_side(item):
    """Parse an MQTT message; key the normalized tick by (symbol, side).

    Maps (topic, payload) -> ("SYMBOL|SIDE", tick) so each symbol/side gets its
    own tumbling window. Returns (key, None) for malformed payloads or
    off-watchlist symbols so the downstream filter can drop them.
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

    return (f"{tick['symbol']}|{int(tick['side'])}", tick)


# ---------- Build dataflow ----------
flow = Dataflow("large_order_ingest")

if config.mock.enabled:
    print("Using MockClickHouseSource for large_order_ingest")
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
        f"Using MqttSource for large_order_ingest — {len(WATCHLIST_SYMBOLS)} symbols, "
        f"min_value={MIN_VALUE}, window={WINDOW_SECONDS}s"
    )
    if not WATCHLIST_SYMBOLS:
        raise SystemExit(
            "large_order_ingest: watchlist is empty — check "
            f"{config.large_order.watchlist_file}"
        )
    topics = [TICK_TOPIC_TEMPLATE.format(symbol=s) for s in WATCHLIST_SYMBOLS]
    stream = op.input(
        "mqtt_ticks",
        flow,
        MqttSource(config.mqtt.host, config.mqtt.port, topics),
    )

# 1) Parse, normalize, key by (symbol, side).
keyed = op.map("key_by_symbol_side", stream, key_by_symbol_side)

# 2) Drop malformed / off-watchlist ticks before windowing.
valid = op.filter("filter_valid", keyed, lambda item: item[1] is not None)

# 2b) Drop ATO/ATC auction prints (single-price clearings, not real blocks).
non_auction = op.filter(
    "drop_auctions",
    valid,
    lambda item: not is_auction_time(item[1]["sending_time"], SESSION_TZ, AUCTION_WINDOWS),
)

# 3) Merge ticks into fixed-second blocks via an event-time tumbling window.
clock = EventClock(
    ts_getter=lambda tick: tick["sending_time"],
    wait_for_system_duration=timedelta(seconds=WAIT_SECONDS),
)
windower = TumblingWindower(
    length=timedelta(seconds=WINDOW_SECONDS),
    align_to=BLOCK_ALIGN_EPOCH,
)
windowed = fold_window(
    "block_fold",
    non_auction,
    clock,
    windower,
    new_block_acc,
    lambda acc, tick: fold_tick(acc, tick, WINDOW_SECONDS),
    merge_acc,
)

# fold_window emits (key, (window_id, accumulator)) on `.down`.
blocks = op.map(
    "finalize_block",
    windowed.down,
    lambda kv: (kv[0], finalize_block(kv[1][1], datetime.now(timezone.utc))),
)

# 4) Keep only blocks whose total notional clears the threshold.
large = op.filter("keep_large_blocks", blocks, lambda kv: is_large_block(kv[1], MIN_VALUE))

# 5) Transform for ClickHouse insertion.
transformed = op.map("to_block_tuple", large, lambda kv: (kv[0], to_block_tuple(kv[1])))

# 6) Sink to ClickHouse large_orders table.
ch_operators.output(
    "large_orders",
    transformed,
    pa_schema=LARGE_ORDERS_ARROW_SCHEMA,
    table_name=LARGE_ORDERS_CLICKHOUSE_TABLE,
    ch_schema=LARGE_ORDERS_CLICKHOUSE_SCHEMA,
    order_by=LARGE_ORDERS_CLICKHOUSE_ORDER_BY,
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
