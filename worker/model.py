import pyarrow as pa

ISP_ALERT_ARROW_SCHEMA = pa.schema([
    ("symbol", pa.string()),
    ("ts", pa.timestamp("us")),
    ("abnormality_ratio_5m", pa.float64()),
    ("abnormality_ratio_15m", pa.float64()),
    ("abnormality_ratio_30m", pa.float64()),
    ("abnormality_ratio_60m", pa.float64()),
    ("z_score_5m", pa.float64()),
    ("z_score_15m", pa.float64()),
    ("z_score_30m", pa.float64()),
    ("z_score_60m", pa.float64()),
])

ISP_ALERT_CLICKHOUSE_SCHEMA = """
    symbol String,
    ts DateTime,
    abnormality_ratio_5m Float64,
    abnormality_ratio_15m Float64,
    abnormality_ratio_30m Float64,
    abnormality_ratio_60m Float64,
    z_score_5m Float64,
    z_score_15m Float64,
    z_score_30m Float64,
    z_score_60m Float64,
"""

ISP_ALERT_CLICKHOUSE_TABLE = "isp_alerts"
ISP_ALERT_CLICKHOUSE_ORDER_BY = "symbol, ts"