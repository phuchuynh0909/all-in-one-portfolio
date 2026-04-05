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
