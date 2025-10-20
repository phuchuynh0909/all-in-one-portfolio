import pyarrow as pa

ISP_ALERT_ARROW_SCHEMA = pa.schema([
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
])

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