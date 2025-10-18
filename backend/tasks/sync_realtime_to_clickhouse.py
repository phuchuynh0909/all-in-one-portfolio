from typing import Iterable, Sequence
import os
import math
import pandas as pd
from prefect import flow, task

from clickhouse_driver import Client  # type: ignore
from metastock2pd import metastock_read, metastock_read_master, metastock_emaster, metastock_xmaster, metastock_master


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


def _normalize_df(
    df: pd.DataFrame,
    symbol: str | None = None,
) -> pd.DataFrame:
    # Accept either separate date/time columns or a ts column
    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    else:
        if not {"date", "time"}.issubset(set(df.columns)):
            raise ValueError("Input must include either 'ts' or both 'date' and 'time' columns")
        df["ts"] = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str), errors="coerce")

    # Symbol from column if present, otherwise provided param
    if "symbol" not in df.columns:
        if symbol is None:
            raise ValueError("'symbol' not in file; please provide symbol argument")
        df["symbol"] = symbol

    # Required numeric fields
    # Some files may use 'size' instead of 'volume'
    if "volume" not in df.columns and "size" in df.columns:
        df["volume"] = df["size"]

    for col in ["price", "volume"]:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Drop rows with invalid ts
    df = df.dropna(subset=["ts"]).copy()
    df = df[["ts", "symbol", "price", "volume"]]
    return df


def _iter_batches(rows: Sequence[tuple], batch_size: int = 50000) -> Iterable[list[tuple]]:
    total = len(rows)
    if total == 0:
        return
    num_batches = math.ceil(total / batch_size)
    for i in range(num_batches):
        start = i * batch_size
        end = min(start + batch_size, total)
        yield rows[start:end]

def _aggregate_to_ohlc_1m(df: pd.DataFrame, symbol: str | None = None) -> pd.DataFrame:
    # Normalize to required tick schema
    norm = _normalize_df(df, symbol=symbol)
    # Ensure correct ordering so first/last per minute are real open/close
    norm = norm.sort_values(["symbol", "ts"]).copy()

    # Group by symbol and 1-minute buckets on ts
    grouped = norm.groupby([
        "symbol",
        pd.Grouper(key="ts", freq="1min"),
    ], dropna=True)

    ohlc = grouped.agg(
        open=("price", "first"),
        high=("price", "max"),
        low=("price", "min"),
        close=("price", "last"),
        volume=("volume", "sum"),
    ).reset_index()

    # Rename grouped ts to plain column name
    ohlc = ohlc.rename(columns={"ts": "ts"})
    # Reorder columns
    ohlc = ohlc[["ts", "symbol", "open", "high", "low", "close", "volume"]]
    return ohlc


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

@task
def write_ohlc1m_to_clickhouse(df: pd.DataFrame, symbol: str | None = None) -> int:
    database = _get_env("CLICKHOUSE_DB", "default")
    table = _get_env("CLICKHOUSE_OHLC_TABLE", "ohlc_1m")

    client = _get_ch_client()
    _ensure_ohlc_table_exists(client, database, table)

    ohlc = _aggregate_to_ohlc_1m(df, symbol=symbol)

    rows: list[tuple] = [
        (
            pd.to_datetime(r.ts).to_pydatetime(),
            str(r.symbol),
            round(float(r.open), 2),
            round(float(r.high), 2),
            round(float(r.low), 2),
            round(float(r.close), 2),
            round(float(r.volume), 2),
            # ver omitted; defaults to now64(3)
        )
        for r in ohlc.itertuples(index=False)
    ]

    inserted = 0
    for batch in _iter_batches(rows, batch_size=50000) or []:
        client.execute(
            f"INSERT INTO {database}.{table} (ts, symbol, open, high, low, close, volume) VALUES",
            batch,
            types_check=False,
        )
        inserted += len(batch)
    return inserted

@task
def collect_metastock_metadata(dir_path: str) -> pd.DataFrame:
    print(f"Collecting MetaStock metadata from {dir_path} ...")
    def get_df_emaster(dir_path) -> pd.DataFrame:
        list_dir = os.listdir(dir_path) 
        df = pd.DataFrame()
        for folder in list_dir:
            folder_path = os.path.join(dir_path, folder)
            if os.path.isdir(folder_path):
                dfTmp = metastock_master(folder_path, encoding='latin1')
                df = pd.concat([df, dfTmp])
            else:
                print("Not a folder: ", folder_path)
        return df

    emaster_df = get_df_emaster(dir_path)
    return emaster_df

@task
def convert_metastock_to_df(symbol: str, metadata_df: pd.DataFrame) -> pd.DataFrame:
    print(f"Converting MetaStock file for {symbol} ...")
    df = metadata_df[metadata_df['symbol'] == symbol]
    file_name = df['filename'].iloc[0]

    tick_df = metastock_read(file_name, fields=4)
    tick_df['ts'] = pd.to_datetime(
        tick_df['date'].astype(str) + ' ' + tick_df['time'].astype(str),
        errors='coerce'
    )
    # tick_df['ts'] = tick_df['ts'].dt.tz_localize('Asia/Ho_Chi_Minh')
    tick_df = tick_df.sort_values(['ts'])

    tick_df = tick_df[['ts', 'price', 'volume']]
    return tick_df

@flow(log_prints=True)
def sync_ohlc1m_df_to_clickhouse() -> int:
    print("Aggregating to 1-minute OHLC and syncing to ClickHouse...")
    
    DNSE_STOCK_DIR = "D:\\dnse\\intraday\\stock"
    metadata_df = collect_metastock_metadata(DNSE_STOCK_DIR)

    watchlist = []
    with open(f"watchlist.csv", "r") as f:
        import csv
        from itertools import chain
        reader = csv.reader(f)
        watchlist = list(chain.from_iterable(reader))
    
    for symbol in watchlist:
        print(f"Processing {symbol} ...")
        try:
            df = convert_metastock_to_df(symbol=symbol, metadata_df=metadata_df)
            count = write_ohlc1m_to_clickhouse(df, symbol=symbol)
            print(f"Inserted {count} OHLC rows into ClickHouse for {symbol}")
        except Exception as e:
            print(e)
            print(f"Error processing {symbol}: {e}")
            continue

# Run the flow
from pathlib import Path

if __name__ == "__main__":
    sync_ohlc1m_df_to_clickhouse.from_source(
        source=str(Path(__file__).parent),  # code stored in local directory
        entrypoint="sync_realtime_to_clickhouse.py:sync_ohlc1m_df_to_clickhouse",
    ).deploy(
        name="sync-ohlc1m-df-to-clickhouse",
        work_pool_name="my-worker",
    )
