import orjson
from datetime import timedelta
from bytewax.dataflow import Dataflow
import bytewax.operators as op
from bytewax.clickhouse import operators as ch_operators

from mqtt_input import MqttSource
from mock_clickhouse import MockClickHouseSource
from config import config
from tick_contract import normalize_tick, to_clickhouse_tuple
from vn30f_symbol import current_symbol as vn30f_current_symbol
from model import (
    TICKS_ARROW_SCHEMA,
    TICKS_CLICKHOUSE_SCHEMA,
    TICKS_CLICKHOUSE_TABLE,
    TICKS_CLICKHOUSE_ORDER_BY,
)

TICK_TOPIC_TEMPLATE = "plaintext/quotes/krx/mdds/tick/v1/roundlot/symbol/{symbol}"


def key_by_symbol_ingest(item):
    """Parse MQTT message and normalize tick data.

    Maps (topic, payload) → (symbol, normalized_dict).
    Returns (symbol, None) for malformed or filtered ticks.
    """
    topic, payload = item
    try:
        raw = orjson.loads(payload)
    except Exception:
        return (config.tick_sync.symbol, None)

    raw_symbol = raw.get("symbol", "")
    if raw_symbol != config.tick_sync.symbol:
        return (raw_symbol, None)

    normalized = normalize_tick(raw)
    if normalized is None:
        return (config.tick_sync.symbol, None)

    return (normalized["symbol"], normalized)


def transform_for_ticks(item):
    """Convert normalized tick to ClickHouse insertion tuple.

    Maps (symbol, tick_dict) → (symbol, clickhouse_tuple).
    """
    symbol, tick_dict = item
    return (symbol, to_clickhouse_tuple(tick_dict))


# ---------- Build dataflow ----------
flow = Dataflow("tick_ingest")

# 1) Ingest from MQTT or Mock source based on configuration
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
    print("Using MqttSource for tick_ingest")
    live_symbol = config.tick_sync.symbol or vn30f_current_symbol()
    tick_topic = TICK_TOPIC_TEMPLATE.format(symbol=live_symbol)
    stream = op.input(
        "mqtt_ticks",
        flow,
        MqttSource(
            config.mqtt.host,
            config.mqtt.port,
            [tick_topic],
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
