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
        # Order book the trade matched on — "G1" main continuous, "G4"/"G7"
        # odd lot, "T1".."T6" put-through. Empty for rows written before this
        # column existed. Last in the tuple because it was added last; see
        # TICKS_ADD_BOARD_ID_DDL.
        pa.field("board_id", pa.string(), nullable=False),
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
    board_id LowCardinality(String) DEFAULT '' CODEC(ZSTD(1)),
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
    received_at DateTime64(6, 'UTC') CODEC(ZSTD(1)),
    -- Which order book matched the trade: "G1" main continuous, "G4"/"G7" odd
    -- lot, "T1".."T6" put-through (negotiated off-book). A handful of distinct
    -- values, so LowCardinality costs almost nothing. Deliberately NOT in the
    -- ORDER BY: that tuple is the ReplacingMergeTree dedup key, and the codecs
    -- above were benchmarked against this exact sort order.
    board_id LowCardinality(String) DEFAULT '' CODEC(ZSTD(1))
)
ENGINE = ReplacingMergeTree(received_at)
ORDER BY (symbol, sending_time, match_price, match_qty, side)
PARTITION BY toYYYYMM(sending_time)
"""

# CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so an
# established deployment needs this as well to gain the column. Adding a column
# is metadata-only in ClickHouse — no rewrite of existing parts — and existing
# rows read back as '' (board unknown, not "G1": they were ingested from a
# nine-board subscription).
TICKS_ADD_BOARD_ID_DDL = """
ALTER TABLE {database}.{table}
ADD COLUMN IF NOT EXISTS board_id LowCardinality(String) DEFAULT '' CODEC(ZSTD(1))
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
# Trade-flow features — replaces the block-episode detector.
#
# Level 1 (`trade_flow_seconds`): 1-second bars, written by a materialized view
# on `ticks`. Every column is mergeable so partial rows from separate INSERTs
# sum/merge exactly — `argMin`/`argMax` states preserve the bar's true first and
# last traded price, which min/max could not, and `quantilesState` lets window
# level quantiles be correct rather than a median-of-medians.
#
# Level 2 (`trade_flow_windows`): a view deriving N-second features. It reads a
# table rather than an insert block, which is what makes ordering across seconds
# — returns, realized volatility, burst intensity — safe to compute.
#
# See core/trade_flow.py for the SELECT bodies and the reasoning.
# ---------------------------------------------------------------------------

TRADE_FLOW_SECONDS_TABLE = "trade_flow_seconds"
TRADE_FLOW_SECONDS_MV = "trade_flow_seconds_mv"
TRADE_FLOW_WINDOWS_VIEW = "trade_flow_windows"

TRADE_FLOW_SECONDS_CREATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {database}.{table} (
    symbol LowCardinality(String) CODEC(ZSTD(1)),
    sec DateTime('UTC') CODEC(Delta, ZSTD(1)),
    trades SimpleAggregateFunction(sum, UInt64) CODEC(T64, ZSTD(1)),
    volume SimpleAggregateFunction(sum, Int64) CODEC(T64, ZSTD(1)),
    notional SimpleAggregateFunction(sum, Float64) CODEC(ZSTD(1)),
    qty_sq SimpleAggregateFunction(sum, Float64) CODEC(ZSTD(1)),
    buy_volume SimpleAggregateFunction(sum, Int64) CODEC(T64, ZSTD(1)),
    sell_volume SimpleAggregateFunction(sum, Int64) CODEC(T64, ZSTD(1)),
    buy_trades SimpleAggregateFunction(sum, UInt64) CODEC(T64, ZSTD(1)),
    sell_trades SimpleAggregateFunction(sum, UInt64) CODEC(T64, ZSTD(1)),
    max_qty SimpleAggregateFunction(max, Int64) CODEC(T64, ZSTD(1)),
    hi SimpleAggregateFunction(max, Float64) CODEC(ZSTD(1)),
    lo SimpleAggregateFunction(min, Float64) CODEC(ZSTD(1)),
    open_px AggregateFunction(argMin, Float64, DateTime64(6, 'UTC')),
    close_px AggregateFunction(argMax, Float64, DateTime64(6, 'UTC')),
    qty_q AggregateFunction(quantiles(0.5, 0.95), Int64),
    -- One millisecond-offset per trade. Merges by concatenation, so the window
    -- view can sort a window's offsets back into arrival order and difference
    -- them for exact inter-arrival gaps — the piece an incremental MV otherwise
    -- cannot produce. Averages ~2.6 values per row on this tape.
    ms_offsets SimpleAggregateFunction(groupArrayArray, Array(UInt16)) CODEC(T64, ZSTD(1))
)
ENGINE = AggregatingMergeTree
ORDER BY (symbol, sec)
PARTITION BY toYYYYMM(sec)
"""

# `{select}` comes from core.trade_flow.second_bar_sql so the feature
# definitions have exactly one home.
TRADE_FLOW_SECONDS_MV_DDL = """
CREATE MATERIALIZED VIEW IF NOT EXISTS {database}.{mv}
TO {database}.{table}
AS
{select}
"""

TRADE_FLOW_WINDOWS_VIEW_DDL = """
CREATE OR REPLACE VIEW {database}.{view} AS
{select}
"""
