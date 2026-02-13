from typing import Any, Generator, Sequence
from pathlib import Path
from prefect import flow, task
# from metastock2pd import metastock_read, metastock_read_master, metastock_emaster
from custom_metastock2pd import metastock_read, metastock_read_master, metastock_emaster, metastock_xmaster

import os
import pandas as pd
import csv
import math
import json
from itertools import chain
from os.path import isfile, join
from metastock import convert_metastock_data
from deltalake import DeltaTable
from clickhouse_driver import Client  # type: ignore
"""Flow: """
# INDEX_DIR = "D:\\fdata_ami\\MetaStock\\EOD\\Chi so"
# INDEX_DIR = "D:\\dnse\\eod\\index"
INDEX_DIR = "D:\\ami\\MetaStock\\EOD\\index"
# INDEX_DIR = "D:\\dnse\\eod\\index"
# STOCK_DIR = "D:\\dnse\\eod\\stock"
# STOCK_DIR = "D:\\fdata_ami\\MetaStock\\EOD\\Co phieu"
STOCK_DIR = "D:\\ami\\MetaStock\\EOD\\stock"
STOCK_BACKUP_DIR = "D:\\ami\\MetaStock\\EOD\\stock"
SYNC_STATE_PATH = os.getenv("DELTA_CDF_SYNC_STATE_PATH", "./.state/ohlc_delta_cdf_sync_state.json")

def get_dir_list(dir_path):
    dir_list = os.listdir(dir_path)
    return dir_list

def get_df_emaster(dir_path) -> pd.DataFrame:
    list_dir = get_dir_list(dir_path)
    df = pd.DataFrame()
    for folder in list_dir:
        folder_path = os.path.join(dir_path, folder)
        if os.path.isdir(folder_path):
            dfTmp = metastock_read_master(folder_path, encoding='latin1')
            df = pd.concat([df, dfTmp])
        else:
            print("Not a folder: ", folder_path)
    return df

def _get_env(name: str, default: str) -> str:
    return os.getenv(name, default)

def _get_ch_client() -> Client:
    host = _get_env("CLICKHOUSE_HOST", "localhost")
    port = int(_get_env("CLICKHOUSE_PORT", "9010"))
    username = _get_env("CLICKHOUSE_USER", "kyostyle1")
    password = _get_env("CLICKHOUSE_PASSWORD", "kyostyle1")
    database = _get_env("CLICKHOUSE_DB", "default")
    return Client(host=host, port=port, user=username, password=password, database=database)

def _get_delta_storage_options() -> dict[str, str]:
    return {
        "AWS_ACCESS_KEY_ID": "CzOwnLkEDXQy951AOqes",
        "AWS_SECRET_ACCESS_KEY": "fdRe91TOtqTl0icUkZLsUnWvZa90aZ5qG5rVEf7S",
        "AWS_ENDPOINT_URL": "http://localhost:9000",
        "AWS_ALLOW_HTTP": "true",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_REGION": 'ap-southeast-1',
        "aws_conditional_put": "etag",
    }

def _load_sync_state(state_path: str) -> dict[str, Any]:
    if not os.path.exists(state_path):
        return {}
    with open(state_path, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_sync_state(state_path: str, state: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f)

def _normalize_cdf_to_ohlc_df(cdf_df: pd.DataFrame) -> pd.DataFrame:
    normalized_input = pd.DataFrame(cdf_df).copy()
    if normalized_input.empty:
        return normalized_input

    if "_change_type" in normalized_input.columns:
        normalized_input = normalized_input[
            normalized_input["_change_type"].isin(["insert", "update_postimage"])
        ].copy()

    required_cols = ["date", "symbol", "open", "high", "low", "close", "volume"]
    missing = [col for col in required_cols if col not in normalized_input.columns]
    if missing:
        raise ValueError(f"CDF output missing required columns: {missing}")

    normalized = pd.DataFrame(normalized_input[required_cols]).copy()
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        normalized[col] = pd.to_numeric(normalized[col], errors="coerce")
    normalized = normalized.dropna(subset=required_cols)
    return normalized

def _insert_ohlc_df_to_clickhouse(df: pd.DataFrame) -> int:
    database = _get_env("CLICKHOUSE_DB", "default")
    table = _get_env("CLICKHOUSE_OHLC_EOD_TABLE", "ohlc_eod")
    client = _get_ch_client()
    _ensure_ohlc_table_exists(client, database, table)

    rows: list[tuple] = [
        (
            pd.to_datetime(r[0]).date(),
            str(r[1]),
            float(r[2]),
            float(r[3]),
            float(r[4]),
            float(r[5]),
            float(r[6]),
        )
        for r in df.itertuples(index=False, name=None)
    ]

    inserted = 0
    for batch in _iter_batches(rows, batch_size=50000) or []:
        client.execute(
            f"INSERT INTO {database}.{table} (date, symbol, open, high, low, close, volume) VALUES",
            batch,
            types_check=False,
        )
        inserted += len(batch)
    return inserted

def _delta_table_to_dataframe(dt: DeltaTable) -> pd.DataFrame:
    arrow_table = dt.to_pyarrow_table()
    to_pandas_fn = getattr(arrow_table, "to_pandas", None)
    if callable(to_pandas_fn):
        return pd.DataFrame(to_pandas_fn())
    return pd.DataFrame(arrow_table)

def _iter_batches(rows: Sequence[tuple], batch_size: int = 50000) -> Generator[list[tuple], None, None]:
    total = len(rows)
    if total == 0:
        return
    num_batches = math.ceil(total / batch_size)
    for i in range(num_batches):
        start = i * batch_size
        end = min(start + batch_size, total)
        yield list(rows[start:end])

def _normalize_ohlc_df(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy()
    try:
        normalized['date'] = pd.to_datetime(normalized['date'], format='%Y%m%d')
    except ValueError:
        normalized['date'] = pd.to_datetime(normalized['date'], errors='coerce')

    for col in ["open", "high", "low", "close", "volume"]:
        normalized[col] = pd.to_numeric(normalized[col], errors='coerce')

    normalized = normalized.dropna(subset=["date", "symbol", "open", "high", "low", "close", "volume"]).copy()
    normalized = pd.DataFrame(normalized[["date", "symbol", "open", "high", "low", "close", "volume"]]).copy()
    return normalized

def _ensure_ohlc_table_exists(client: Client, database: str, table: str) -> None:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
    client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {database}.{table} (
            date Date,
            symbol String,
            open Float64,
            high Float64,
            low Float64,
            close Float64,
            volume Float64,
            ver DateTime64(3) DEFAULT now64(3)
        )
        ENGINE = ReplacingMergeTree(ver)
        PARTITION BY intDiv(toYear(date) - 1970, 5)
        ORDER BY (symbol, date)
        """
    )

@task
def sync_to_delta_table(df: pd.DataFrame, destination = "s3://delta-table-storage/stocks") -> None:
    """Sync data to Delta table"""

    # Transform date column to datetime
    # Parse date with explicit format for YYYYMMDD
    try:
        df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
    except ValueError:
        # Fallback to automatic parsing if format doesn't match
        df['date'] = pd.to_datetime(df['date'])

    df['key'] = df["symbol"] + "_" + df['date'].dt.strftime('%Y-%m-%d')
    # Create a new column 'key' by concatenating 'symbol' and formatted 'date']

    ## select only the columns we need
    df = pd.DataFrame(df[["key", "symbol", "date", "open", "high", "low", "close", "volume"]]).copy()

    storage_options = _get_delta_storage_options()

    # print(destination)
    dt = DeltaTable(destination, storage_options=storage_options)
    result = dt.merge(df, 
            predicate="target.key == source.key",
            source_alias="source",
            target_alias="target"
    ) \
        .when_not_matched_insert_all() \
        .when_matched_update_all(predicate="target.key == source.key " \
            "AND target.volume != source.volume AND target.close != source.close")\
        .execute()
    print(result)

    ## vacuum the table
    vacum_result = dt.vacuum(retention_hours=24, dry_run=False, enforce_retention_duration=False)
    print(vacum_result)

    compact = dt.optimize.compact()
    print(compact)

@task(log_prints=True)
def sync_delta_cdf_to_clickhouse(
    destination: str = "s3://delta-table-storage/stocks",
    state_path: str = SYNC_STATE_PATH,
) -> int:
    storage_options = _get_delta_storage_options()
    dt = DeltaTable(destination, storage_options=storage_options)

    metadata = dt.metadata()
    cdf_enabled = str(metadata.configuration.get("delta.enableChangeDataFeed", "false")).lower() == "true"
    if not cdf_enabled:
        raise RuntimeError(
            "Delta CDF is not enabled. Set table property 'delta.enableChangeDataFeed=true' before using incremental sync."
        )

    latest_version = dt.version()
    state = _load_sync_state(state_path)
    last_synced_version = state.get("last_synced_version")
    full_load_on_first_run = _get_env("DELTA_CDF_FULL_LOAD_ON_FIRST_RUN", "false").lower() in {
        "1", "true", "yes", "y"
    }

    if last_synced_version is None and full_load_on_first_run:
        snapshot_df = _delta_table_to_dataframe(dt)
        normalized_snapshot = _normalize_ohlc_df(snapshot_df)
        inserted = _insert_ohlc_df_to_clickhouse(normalized_snapshot)
        _save_sync_state(state_path, {"last_synced_version": int(latest_version)})
        print(
            f"First run full load enabled. Inserted {inserted} rows and set last_synced_version={latest_version}"
        )
        return inserted

    start_version = int(last_synced_version) + 1 if last_synced_version is not None else int(latest_version)
    if start_version > int(latest_version):
        print(f"No new Delta versions to sync. last={last_synced_version}, latest={latest_version}")
        return 0

    cdf_reader = dt.load_cdf(starting_version=start_version, ending_version=int(latest_version))
    cdf_arrow = cdf_reader.read_all()
    if cdf_arrow is None:
        cdf_df = pd.DataFrame()
    else:
        to_pandas_fn = getattr(cdf_arrow, "to_pandas", None)
        if callable(to_pandas_fn):
            cdf_df = pd.DataFrame(to_pandas_fn())
        else:
            cdf_df = pd.DataFrame(cdf_arrow)
    normalized = _normalize_cdf_to_ohlc_df(cdf_df)

    if normalized.empty:
        _save_sync_state(state_path, {"last_synced_version": int(latest_version)})
        print(f"No CDF rows to sync for versions {start_version}..{latest_version}")
        return 0

    inserted = _insert_ohlc_df_to_clickhouse(normalized)

    _save_sync_state(state_path, {"last_synced_version": int(latest_version)})
    print(f"Synced Delta CDF versions {start_version}..{latest_version} into ClickHouse: {inserted} rows")
    return inserted

@task(log_prints=True)
def sync_to_clickhouse(df: pd.DataFrame) -> int:
    database = _get_env("CLICKHOUSE_DB", "default")
    table = _get_env("CLICKHOUSE_OHLC_EOD_TABLE", "ohlc_eod")
    client = _get_ch_client()
    _ensure_ohlc_table_exists(client, database, table)

    normalized = _normalize_ohlc_df(df)
    rows: list[tuple] = [
        (
            pd.to_datetime(r[0]).date(),
            str(r[1]),
            float(r[2]),
            float(r[3]),
            float(r[4]),
            float(r[5]),
            float(r[6]),
        )
        for r in normalized.itertuples(index=False, name=None)
    ]

    inserted = 0
    for batch in _iter_batches(rows, batch_size=50000) or []:
        client.execute(
            f"INSERT INTO {database}.{table} (date, symbol, open, high, low, close, volume) VALUES",
            batch,
            types_check=False,
        )
        inserted += len(batch)
    print(f"Inserted {inserted} rows into ClickHouse table {database}.{table}")
    return inserted

@task
def convert_metastock_to_df() -> pd.DataFrame:
    """Convert a MetaStock file to a DataFrame."""
    
    ## Get watchlist stock symbols
    watchlist = []
    with open(f"D:\\Projects\\trading_toolbox\\watchlist.csv", "r") as f:
        reader = csv.reader(f)
        watchlist = list(chain.from_iterable(reader))
    all_symbol_ticker_df = pd.DataFrame()

    DNSE_STOCK_DIR = "D:\\dnse\\eod\\stock"
    emaster_df = get_df_emaster(DNSE_STOCK_DIR)
    df = emaster_df.query('symbol in @watchlist')
    for index, row in df.iterrows():
        print("Processing " , row["symbol"], " ...")
        fileName = row["filename"]
        if isinstance(fileName, pd.Series):
            fileName = row["filename"].iloc[0]
        try:
            tickDf = metastock_read(fileName, extra_buffer=50)
            tickDf = tickDf.sort_index().tail(50).reset_index(names='date')
            tickDf['symbol'] = row['symbol']
            all_symbol_ticker_df = pd.concat([all_symbol_ticker_df, tickDf])
        except Exception as e:
            print(e)
            print("Cannot read file: ", fileName)

    # Convert index data
    DNSE_INDEX_DIR = "D:\\dnse\\eod\\index"
    emaster_index_df = metastock_emaster(DNSE_INDEX_DIR)
    for index, row in emaster_index_df.iterrows():
        try:
            print("Processing " , row["symbol"], " ...")
            tickDf = metastock_read(row["filename"], extra_buffer=50)
            tickDf = tickDf.sort_index().tail(50).reset_index(names='date')
            tickDf['symbol'] = row['symbol']
            all_symbol_ticker_df = pd.concat([all_symbol_ticker_df, tickDf])
        except Exception as e:
            print(e)
            print("Cannot read file: ", row["filename"])

    # Get Index data from Fdata
    FDATA_INDEX_DIR = "D:\\fdata_ami\\MetaStock\\EOD\\Chi so"
    emaster_index_df = metastock_read_master(FDATA_INDEX_DIR)
    for index, row in emaster_index_df.iterrows():
        try:
            # skip specific index symbols
            if row["symbol"] in ["VNINDEX", "VN30"]:
                continue
            print("Processing " , row["symbol"], " ...")
            tickDf = metastock_read(row["filename"], extra_buffer=50)
            tickDf = tickDf.sort_index().tail(50).reset_index(names='date')
            tickDf['symbol'] = row['symbol']
            all_symbol_ticker_df = pd.concat([all_symbol_ticker_df, tickDf])
        except Exception as e:
            print(e)
            print("Cannot read file: ", row["filename"])

    all_symbol_ticker_df['volume'] = all_symbol_ticker_df['volume'].astype('float64')
    return all_symbol_ticker_df

@flow(log_prints=True)
def sync_ticker_delta_table_pipeline(destination: str = "s3://delta-table-storage/stocks") -> None:
    """Flow: ETL for syncing tickers"""
     
    # Task 1: Collect data from MetaStock files
    df = convert_metastock_to_df()

    # Task 2: Sync data to Delta table
    sync_to_delta_table(df=df, destination=destination)

    # Task 3: Sync only Delta CDF changes to ClickHouse
    sync_delta_cdf_to_clickhouse(destination=destination)


# Run the flow
if __name__ == "__main__":
    # sync_ticker_delta_table_pipeline(destination="s3://delta-table-storage/stocks")

    sync_ticker_delta_table_pipeline.from_source(
        source=str(Path(__file__).parent),  # code stored in local directory
        entrypoint="crawl_ohlc_data.py:sync_ticker_delta_table_pipeline",
    ).deploy(
        name="sync-ticker-delta-table",
        work_pool_name="my-worker",
        # Run each hour from 10:00 to 15:00 every monday to friday
        # convert it from UTC to local time
        cron="0 8-9 * * 1-5",
    )
