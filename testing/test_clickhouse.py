from clickhouse_driver import Client  # type: ignore
import os


def _ensure_ohlc_table_exists(client: Client, database: str, table: str) -> None:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
    client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {database}.{table} (
            ts DateTime,
            symbol String,
            open Float64,
            high Float64,
            low Float64,
            close Float64,
            volume Float64,
            ver DateTime64(3) DEFAULT now64(3)
        )
        ENGINE = ReplacingMergeTree(ver)
        PARTITION BY symbol
        ORDER BY (ts)
        """
    )

def _get_env(name: str, default: str) -> str:
    val = os.getenv(name, default)
    return val


def _get_ch_client():
    host = _get_env("CLICKHOUSE_HOST", "localhost")
    port = int(_get_env("CLICKHOUSE_PORT", "9010"))  # native port for driver
    username = _get_env("CLICKHOUSE_USER", "kyostyle1")
    password = _get_env("CLICKHOUSE_PASSWORD", "kyostyle1")
    database = _get_env("CLICKHOUSE_DB", "default")
    return Client(host=host, port=port, user=username, password=password, database=database)

database = _get_env("CLICKHOUSE_DB", "default")
table = _get_env("CLICKHOUSE_OHLC_TABLE", "ohlc_1m")

client = _get_ch_client()
print(f"Ensuring ClickHouse table {database}.{table} exists...")
_ensure_ohlc_table_exists(client, database, table)
print("Done.")