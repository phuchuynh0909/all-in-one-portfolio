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
        # Plain string on the wire; ClickHouse converts it into the table's
        # LowCardinality(String) column on insert. Arrow dictionary encoding
        # also works but buys nothing here — the batches are small.
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

# Per-column codecs are benchmarked, not guessed — see TICKS_CREATE_TABLE_DDL.
TICKS_CLICKHOUSE_SCHEMA = """
    symbol LowCardinality(String) CODEC(ZSTD(1)),
    sending_time DateTime64(6, 'UTC') CODEC(Delta, ZSTD(1)),
    match_price Float64 CODEC(ZSTD(1)),
    match_qty Int64 CODEC(T64, ZSTD(1)),
    side Int32 CODEC(T64, ZSTD(1)),
    received_at DateTime64(6, 'UTC') CODEC(ZSTD(1)),
"""

TICKS_CLICKHOUSE_TABLE = "ticks"

TICKS_CLICKHOUSE_ORDER_BY = "symbol, sending_time, match_price, match_qty, side"

# Codecs below were picked by benchmarking every plausible candidate against 5M
# real tick rows spread over ~200 symbols (the post-watchlist shape), measuring
# system.parts_columns. Two results are counter-intuitive enough to record:
#
#   * DoubleDelta LOSES on every column here. It encodes the second derivative,
#     so it only pays when intervals are near-constant. Ticks are irregular, and
#     because ORDER BY leads with `symbol`, sending_time restarts at every
#     symbol boundary — ~200 resets per part. Measured vs plain ZSTD(1):
#     sending_time 90%, match_qty 142%, received_at 124%.
#   * Gorilla LOSES on match_price (115% of plain ZSTD). Its XOR output is
#     less compressible for the round, repeated prices in this tape than the
#     raw values are; FPC merely ties ZSTD (99%), so neither earns its place.
#
# Winners, vs plain ZSTD(1): Delta on sending_time 79%, T64 on match_qty 77%,
# T64 on side 75%. Floats and received_at keep plain ZSTD(1).
# Re-measure before changing these — the answer depends on the sort order.
TICKS_CREATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {database}.{table} (
    -- ~200 distinct symbols (watchlist + VN30F contracts): well inside the
    -- range where LowCardinality's dictionary encoding pays off.
    symbol LowCardinality(String) CODEC(ZSTD(1)),
    -- Delta, not DoubleDelta: irregular tick spacing + per-symbol resets.
    sending_time DateTime64(6, 'UTC') CODEC(Delta, ZSTD(1)),
    -- Gorilla/FPC measured no better than plain ZSTD on this price tape.
    match_price Float64 CODEC(ZSTD(1)),
    match_qty Int64 CODEC(T64, ZSTD(1)),
    side Int32 CODEC(T64, ZSTD(1)),
    -- Insert-ordered, so unsorted within a part: delta codecs backfire.
    received_at DateTime64(6, 'UTC') CODEC(ZSTD(1))
)
ENGINE = ReplacingMergeTree(received_at)
ORDER BY (symbol, sending_time, match_price, match_qty, side)
PARTITION BY toYYYYMM(sending_time)
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
# Large-order blocks — the *live* path, a materialized view over `ticks`.
#
# Why AggregatingMergeTree and not the ReplacingMergeTree `large_orders` table:
# a materialized view sees only the rows of one INSERT. `tick_ingest` flushes
# every ~2s while blocks are 1s wide, so one bucket's fills routinely arrive
# across several inserts and the view emits a *partial* block each time.
# SimpleAggregateFunction(sum) makes those partials additive; Replacing would
# overwrite them and silently undercount.
#
# For the same reason the threshold is NOT applied here — a partial can sit
# below LARGE_ORDER_MIN_VALUE while the finished block clears it. Filtering
# happens at read time, in LARGE_ORDERS_LIVE_VIEW_DDL.
#
# `vwap` is absent: a ratio is not summable. It is derived on read.
# ---------------------------------------------------------------------------

LARGE_ORDER_BLOCKS_TABLE = "large_order_blocks"
LARGE_ORDER_BLOCKS_MV = "large_order_blocks_mv"
LARGE_ORDERS_LIVE_VIEW = "large_orders_live"

LARGE_ORDER_BLOCKS_CREATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {database}.{table} (
    symbol LowCardinality(String) CODEC(ZSTD(1)),
    sending_time DateTime64(6, 'UTC') CODEC(Delta, ZSTD(1)),
    side Int32 CODEC(T64, ZSTD(1)),
    total_qty SimpleAggregateFunction(sum, Int64) CODEC(T64, ZSTD(1)),
    dollar_value SimpleAggregateFunction(sum, Float64) CODEC(ZSTD(1)),
    num_trades SimpleAggregateFunction(sum, UInt64) CODEC(T64, ZSTD(1))
)
ENGINE = AggregatingMergeTree
ORDER BY (symbol, sending_time, side)
PARTITION BY toYYYYMM(sending_time)
"""

# `{select}` is rendered by core.large_order.block_aggregation_sql so the
# bucketing and auction rules have exactly one definition.
LARGE_ORDER_BLOCKS_MV_DDL = """
CREATE MATERIALIZED VIEW IF NOT EXISTS {database}.{mv}
TO {database}.{table}
AS
{select}
"""

# Serving shape, column-compatible with `large_orders` (minus received_at).
#
# The GROUP BY is required, not cosmetic: AggregatingMergeTree merges parts in
# the background, so a plain SELECT would read unmerged partials and report
# several small blocks where there is one. Aggregating on read is always
# correct — unlike FINAL, it cannot be forgotten by a caller.
# The aggregation sits in a subquery because `sum(total_qty) AS total_qty`
# shadows the column, and deriving vwap from that alias in the same SELECT is
# rejected as a nested aggregate (ILLEGAL_AGGREGATION). vwap yields 0.0 on zero
# quantity, matching `core.large_order.finalize_block`.
LARGE_ORDERS_LIVE_VIEW_DDL = """
CREATE OR REPLACE VIEW {database}.{view} AS
SELECT
    symbol,
    sending_time,
    side,
    if(total_qty = 0, 0.0, dollar_value / total_qty) AS vwap,
    total_qty,
    dollar_value,
    num_trades
FROM (
    SELECT
        symbol,
        sending_time,
        side,
        sum(total_qty) AS total_qty,
        sum(dollar_value) AS dollar_value,
        sum(num_trades) AS num_trades
    FROM {database}.{table}
    GROUP BY symbol, sending_time, side
)
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
