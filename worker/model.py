import pyarrow as pa

ISP_ALERT_ARROW_SCHEMA = pa.schema(
    [
        ("symbol", pa.string()),
        ("ts", pa.timestamp("us")),
        ("ofi_5s", pa.float64()),
        ("abnormality_ratio_5m", pa.float64()),
        ("abnormality_ratio_15m", pa.float64()),
        ("abnormality_ratio_30m", pa.float64()),
        ("abnormality_ratio_60m", pa.float64()),
        ("z_score_5m", pa.float64()),
        ("z_score_15m", pa.float64()),
        ("z_score_30m", pa.float64()),
        ("z_score_60m", pa.float64()),
        ("rvol_5m", pa.float64()),
        ("rvol_15m", pa.float64()),
        ("rvol_30m", pa.float64()),
        ("rvol_60m", pa.float64()),
        ("realized_volume_5s", pa.float64()),
        ("expected_volume_5s", pa.float64()),
        ("surge_ratio_5s", pa.float64()),
        ("z_score_5s", pa.float64()),
        ("tick_count_5s", pa.int64()),
    ]
)

ISP_ALERT_CLICKHOUSE_SCHEMA = """
    symbol String,
    ts DateTime,
    ofi_5s Float64,
    abnormality_ratio_5m Float64,
    abnormality_ratio_15m Float64,
    abnormality_ratio_30m Float64,
    abnormality_ratio_60m Float64,
    z_score_5m Float64,
    z_score_15m Float64,
    z_score_30m Float64,
    z_score_60m Float64,
    rvol_5m Float64,
    rvol_15m Float64,
    rvol_30m Float64,
    rvol_60m Float64,
    realized_volume_5s Float64,
    expected_volume_5s Float64,
    surge_ratio_5s Float64,
    z_score_5s Float64,
    tick_count_5s Int64,
"""

ISP_ALERT_CLICKHOUSE_TABLE = "isp_alerts"
ISP_ALERT_CLICKHOUSE_ORDER_BY = "symbol, ts"

TICKS_ARROW_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("sending_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("match_price", pa.float64(), nullable=False),
        # Non-nullable: ClickHouse ORDER BY does not allow Nullable columns.
        # Use 0 as the sentinel value for unknown/missing qty.
        pa.field("match_qty", pa.int64(), nullable=False),
        # Non-nullable: use 0 as sentinel (0=unknown, 1=BUY, 2=SELL).
        pa.field("side", pa.int32(), nullable=False),
        pa.field("received_at", pa.timestamp("us", tz="UTC"), nullable=False),
    ]
)

TICKS_CLICKHOUSE_SCHEMA = """
    symbol String,
    sending_time DateTime64(6, 'UTC'),
    match_price Float64,
    match_qty Int64,
    side Int32,
    received_at DateTime64(6, 'UTC'),
"""

TICKS_CLICKHOUSE_TABLE = "ticks"

TICKS_CLICKHOUSE_ORDER_BY = "symbol, sending_time, match_price, match_qty, side"

TICKS_CREATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {database}.ticks (
    symbol String,
    sending_time DateTime64(6, 'UTC'),
    match_price Float64,
    match_qty Int64,
    side Int32,
    received_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(received_at)
ORDER BY (symbol, sending_time, match_price, match_qty, side)
PARTITION BY toYYYYMMDD(sending_time)
"""

# ---------------------------------------------------------------------------
# Large orders ("Layer 3" block tape) — trades are merged into fixed-second
# blocks per (symbol, side, time-bucket); only blocks whose total notional
# value clears a configurable threshold are stored. One row per block:
#   sending_time = bucket start, vwap = volume-weighted price,
#   total_qty / dollar_value = block sums, num_trades = fills merged.
# ---------------------------------------------------------------------------

LARGE_ORDERS_ARROW_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("sending_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("side", pa.int32(), nullable=False),
        pa.field("vwap", pa.float64(), nullable=False),
        pa.field("total_qty", pa.int64(), nullable=False),
        pa.field("dollar_value", pa.float64(), nullable=False),
        pa.field("num_trades", pa.int64(), nullable=False),
        pa.field("received_at", pa.timestamp("us", tz="UTC"), nullable=False),
    ]
)

LARGE_ORDERS_CLICKHOUSE_SCHEMA = """
    symbol String,
    sending_time DateTime64(6, 'UTC'),
    side Int32,
    vwap Float64,
    total_qty Int64,
    dollar_value Float64,
    num_trades Int64,
    received_at DateTime64(6, 'UTC'),
"""

LARGE_ORDERS_CLICKHOUSE_TABLE = "large_orders"

# One block per (symbol, bucket, side) — that tuple is the dedup key.
# ReplacingMergeTree(received_at) lets the reconciler overwrite a partial
# live block with the authoritative end-of-day aggregate.
LARGE_ORDERS_CLICKHOUSE_ORDER_BY = "symbol, sending_time, side"

LARGE_ORDERS_CREATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {database}.large_orders (
    symbol String,
    sending_time DateTime64(6, 'UTC'),
    side Int32,
    vwap Float64,
    total_qty Int64,
    dollar_value Float64,
    num_trades Int64,
    received_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(received_at)
ORDER BY (symbol, sending_time, side)
PARTITION BY toYYYYMMDD(sending_time)
"""


# ---------------------------------------------------------------------------
# Block episodes ("large-execution footprint") — stitched, same-direction
# candidate bins produced by core.large_execution.detect. One row per episode.
# A block episode is a *footprint* of sustained/one-sided execution or an
# outlier large print — NOT proof of an institution or a parent order.
# ---------------------------------------------------------------------------

BLOCK_EPISODES_COLUMNS = [
    "symbol",
    "start_time",
    "end_time",
    "side",
    "candidate_type",
    "signed_notional",
    "abs_notional",
    "num_trades",
    "num_bins",
    "large_print_count",
    "max_abs_z",
    "max_abs_imbalance",
    "received_at",
]

BLOCK_EPISODES_ARROW_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("start_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("end_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("side", pa.int32(), nullable=False),
        pa.field("candidate_type", pa.string(), nullable=False),
        pa.field("signed_notional", pa.float64(), nullable=False),
        pa.field("abs_notional", pa.float64(), nullable=False),
        pa.field("num_trades", pa.int64(), nullable=False),
        pa.field("num_bins", pa.int64(), nullable=False),
        pa.field("large_print_count", pa.int64(), nullable=False),
        pa.field("max_abs_z", pa.float64(), nullable=False),
        pa.field("max_abs_imbalance", pa.float64(), nullable=False),
        pa.field("received_at", pa.timestamp("us", tz="UTC"), nullable=False),
    ]
)

BLOCK_EPISODES_CLICKHOUSE_SCHEMA = """
    symbol String,
    start_time DateTime64(6, 'UTC'),
    end_time DateTime64(6, 'UTC'),
    side Int32,
    candidate_type String,
    signed_notional Float64,
    abs_notional Float64,
    num_trades Int64,
    num_bins Int64,
    large_print_count Int64,
    max_abs_z Float64,
    max_abs_imbalance Float64,
    received_at DateTime64(6, 'UTC'),
"""

BLOCK_EPISODES_CLICKHOUSE_TABLE = "block_episodes"

# One episode per (symbol, start_time, side) — that tuple is the dedup key.
# ReplacingMergeTree(received_at) lets a rerun overwrite an earlier episode
# whose bounds/aggregates changed as more of the day's tape arrived.
BLOCK_EPISODES_CLICKHOUSE_ORDER_BY = "symbol, start_time, side"

BLOCK_EPISODES_CREATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {database}.block_episodes (
    symbol String,
    start_time DateTime64(6, 'UTC'),
    end_time DateTime64(6, 'UTC'),
    side Int32,
    candidate_type String,
    signed_notional Float64,
    abs_notional Float64,
    num_trades Int64,
    num_bins Int64,
    large_print_count Int64,
    max_abs_z Float64,
    max_abs_imbalance Float64,
    received_at DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(received_at)
ORDER BY (symbol, start_time, side)
PARTITION BY toYYYYMMDD(start_time)
"""
