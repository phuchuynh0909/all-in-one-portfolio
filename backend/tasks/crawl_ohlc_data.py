from typing import Any
from pathlib import Path
import gc
from prefect import flow, task
# from metastock2pd import metastock_read, metastock_read_master, metastock_emaster
from custom_metastock2pd import metastock_read, metastock_read_master, metastock_emaster, metastock_xmaster

import os
import pandas as pd
import csv
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
    parts: list[pd.DataFrame] = []
    for folder in list_dir:
        folder_path = os.path.join(dir_path, folder)
        if os.path.isdir(folder_path):
            df_tmp = metastock_read_master(folder_path, encoding='latin1')
            parts.append(df_tmp)
        else:
            print("Not a folder: ", folder_path)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)

def _get_env(name: str, default: str) -> str:
    return os.getenv(name, default)

def _get_ch_client() -> Client:
    host = _get_env("CLICKHOUSE_HOST", "localhost")
    port = int(_get_env("CLICKHOUSE_PORT", "9000"))   # native TCP port (not HTTP 8123)
    user = _get_env("CLICKHOUSE_USER", "kyostyle1")
    password = _get_env("CLICKHOUSE_PASSWORD", "kyostyle1")
    database = _get_env("CLICKHOUSE_DB", "default")
    try:
        return Client(host=host, port=port, user=user, password=password, database=database)
    except Exception as e:
        raise RuntimeError(
            f"ClickHouse connection failed at {host}:{port}. "
            f"Override with CLICKHOUSE_HOST / CLICKHOUSE_PORT env vars. "
            f"Port must be the native TCP port (default 9000), not HTTP (8123). "
            f"Error: {e}"
        ) from e

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
    if cdf_df.empty:
        return cdf_df

    normalized_input = cdf_df
    if "_change_type" in normalized_input.columns:
        normalized_input = normalized_input[
            normalized_input["_change_type"].isin(["insert", "update_postimage"])
        ]

    required_cols = ["date", "symbol", "open", "high", "low", "close", "volume"]
    missing = [col for col in required_cols if col not in normalized_input.columns]
    if missing:
        raise ValueError(f"CDF output missing required columns: {missing}")

    normalized = normalized_input[required_cols].copy()
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        normalized[col] = pd.to_numeric(normalized[col], errors="coerce")
    normalized = normalized.dropna(subset=required_cols)
    return normalized

def _insert_ohlc_df_to_clickhouse(df: pd.DataFrame, batch_size: int = 50000) -> int:
    """Insert OHLC rows without materializing the full table as a Python tuple list."""
    database = _get_env("CLICKHOUSE_DB", "default")
    table = _get_env("CLICKHOUSE_OHLC_EOD_TABLE", "ohlc_eod")
    client = _get_ch_client()
    _ensure_ohlc_table_exists(client, database, table)

    inserted = 0
    n = len(df)
    for start in range(0, n, batch_size):
        chunk = df.iloc[start : start + batch_size]
        rows = [
            (
                pd.to_datetime(r[0]).date(),
                str(r[1]),
                float(r[2]),
                float(r[3]),
                float(r[4]),
                float(r[5]),
                float(r[6]),
            )
            for r in chunk.itertuples(index=False, name=None)
        ]
        if not rows:
            continue
        client.execute(
            f"INSERT INTO {database}.{table} (date, symbol, open, high, low, close, volume) VALUES",
            rows,
            types_check=False,
        )
        inserted += len(rows)
    return inserted

def _delta_table_to_dataframe(dt: DeltaTable) -> pd.DataFrame:
    arrow_table = dt.to_pyarrow_table()
    to_pandas_fn = getattr(arrow_table, "to_pandas", None)
    if callable(to_pandas_fn):
        return pd.DataFrame(to_pandas_fn())
    return pd.DataFrame(arrow_table)

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

def _env_flag(name: str, default: str = "false") -> bool:
    return _get_env(name, default).lower() in {"1", "true", "yes", "y"}


@task
def sync_to_delta_table(df: pd.DataFrame, destination = "s3://delta-table-storage/stocks") -> None:
    """Merge OHLC upserts into Delta.

    Vacuum + optimize.compact() are **off by default**: they rewrite large parts of the table and
    routinely OOM incremental syncs. Enable with DELTA_SYNC_RUN_VACUUM / DELTA_SYNC_RUN_OPTIMIZE,
    or run maintenance in a separate scheduled job.
    """

    import pyarrow as pa
    from deltalake.writer import write_deltalake
    from deltalake.exceptions import TableNotFoundError

    try:
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    except ValueError:
        df["date"] = pd.to_datetime(df["date"])

    df["key"]  = df["symbol"] + "_" + df["date"].dt.strftime("%Y-%m-%d")
    df["year"] = df["date"].dt.year.astype(str)
    df = df[["key", "symbol", "date", "year", "open", "high", "low", "close", "volume"]]

    storage_options = _get_delta_storage_options()

    try:
        dt = DeltaTable(destination, storage_options=storage_options)
        table_exists = True
    except TableNotFoundError:
        table_exists = False
        print(f"Table not found at {destination} — will create on first write.")

    years = sorted(df["year"].unique())

    if not table_exists:
        print(f"Creating year-partitioned table with {len(df):,} rows …")
        arrow_table = pa.Table.from_pandas(df, preserve_index=False)
        write_deltalake(
            destination,
            arrow_table,
            mode="overwrite",
            partition_by=["year"],
            storage_options=storage_options,
            engine="rust",
            configuration={"delta.enableChangeDataFeed": "true"},
        )
        print(f"  Table created: {len(arrow_table):,} rows, partitions: {years}")
        del arrow_table
    else:
        print(f"Merging {len(years)} year(s) into Delta …")
        for year in years:
            year_df = df[df["year"] == year].copy()

            result = (
                dt.merge(
                    year_df,
                    predicate=f"target.key == source.key AND target.year = '{year}'",
                    source_alias="source",
                    target_alias="target",
                )
                .when_not_matched_insert_all()
                .when_matched_update_all(
                    predicate="target.volume != source.volume OR target.close != source.close"
                )
                .execute()
            )
            print(f"  {year}: {result}")
            del year_df, result
            gc.collect()

    del df
    gc.collect()

    run_vacuum  = _env_flag("DELTA_SYNC_RUN_VACUUM",  "false")
    run_optimize = _env_flag("DELTA_SYNC_RUN_OPTIMIZE", "false")

    if run_vacuum or run_optimize:
        dt = DeltaTable(destination, storage_options=storage_options)
        if run_vacuum:
            print(dt.vacuum(retention_hours=24, dry_run=False, enforce_retention_duration=False))
            gc.collect()
        if run_optimize:
            print(dt.optimize.compact())
            gc.collect()

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
        print("CDF not enabled — enabling now and bookmarking current version.")
        dt.alter.set_table_properties({"delta.enableChangeDataFeed": "true"})
        _save_sync_state(state_path, {"last_synced_version": int(dt.version())})
        return 0

    latest_version = dt.version()
    state = _load_sync_state(state_path)
    last_synced_version = state.get("last_synced_version")
    full_load_on_first_run = _get_env("DELTA_CDF_FULL_LOAD_ON_FIRST_RUN", "false").lower() in {
        "1", "true", "yes", "y"
    }

    if last_synced_version is None and full_load_on_first_run:
        snapshot_df = _delta_table_to_dataframe(dt)
        normalized_snapshot = _normalize_ohlc_df(snapshot_df)
        del snapshot_df
        inserted = _insert_ohlc_df_to_clickhouse(normalized_snapshot)
        del normalized_snapshot
        gc.collect()
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
    del cdf_arrow, cdf_reader
    normalized = _normalize_cdf_to_ohlc_df(cdf_df)
    del cdf_df

    if normalized.empty:
        _save_sync_state(state_path, {"last_synced_version": int(latest_version)})
        print(f"No CDF rows to sync for versions {start_version}..{latest_version}")
        return 0

    inserted = _insert_ohlc_df_to_clickhouse(normalized)
    del normalized
    gc.collect()

    _save_sync_state(state_path, {"last_synced_version": int(latest_version)})
    print(f"Synced Delta CDF versions {start_version}..{latest_version} into ClickHouse: {inserted} rows")
    return inserted

@task(log_prints=True)
def sync_to_clickhouse(df: pd.DataFrame) -> int:
    normalized = _normalize_ohlc_df(df)
    inserted = _insert_ohlc_df_to_clickhouse(normalized)
    database = _get_env("CLICKHOUSE_DB", "default")
    table = _get_env("CLICKHOUSE_OHLC_EOD_TABLE", "ohlc_eod")
    print(f"Inserted {inserted} rows into ClickHouse table {database}.{table}")
    return inserted

@task
@task
def convert_metastock_to_df(full_refresh: bool = False) -> pd.DataFrame:
    """Convert MetaStock files to a DataFrame.

    Args:
        full_refresh: When True, load all available history with no date cutoff.
                      When False (default), load only the last max_days of data.
    """
    max_days = 20
    cutoff   = None if full_refresh else pd.Timestamp.today().normalize() - pd.Timedelta(days=max_days)

    if full_refresh:
        print("Full refresh — loading all available history (no cutoff)")
    else:
        print(f"Incremental — loading from {cutoff.date()} onwards")

    ## Get watchlist stock symbols
    with open(f"D:\\Projects\\trading_toolbox\\watchlist.csv", "r") as f:
        reader = csv.reader(f)
        watchlist: list[str] = list(chain.from_iterable(reader))
    # Append per-symbol frames then concat once — repeated pd.concat in a loop copies O(n²) data.
    frames: list[pd.DataFrame] = []

    def _apply_cutoff(tick: pd.DataFrame) -> pd.DataFrame:
        tick = tick.sort_index()
        if cutoff is not None:
            tick = tick[tick.index >= cutoff]
        return tick.reset_index(names='date')

    DNSE_STOCK_DIR = "D:\\dnse\\eod\\stock"
    emaster_df = get_df_emaster(DNSE_STOCK_DIR)
    df = emaster_df.query('symbol in @watchlist')
    del emaster_df
    for _, row in df.iterrows():
        print("Processing " , row["symbol"], " ...")
        fileName = row["filename"]
        if isinstance(fileName, pd.Series):
            fileName = row["filename"].iloc[0]
        try:
            tickDf = metastock_read(fileName, extra_buffer=50)
            tickDf = _apply_cutoff(tickDf)
            tickDf['symbol'] = row['symbol']
            frames.append(tickDf)
        except Exception as e:
            print(e)
            print("Cannot read file: ", fileName)
    del df

    # Convert index data
    DNSE_INDEX_DIR = "D:\\dnse\\eod\\index"
    emaster_index_df = metastock_emaster(DNSE_INDEX_DIR)
    for _, row in emaster_index_df.iterrows():
        try:
            print("Processing " , row["symbol"], " ...")
            tickDf = metastock_read(row["filename"], extra_buffer=50)
            tickDf = _apply_cutoff(tickDf)
            tickDf['symbol'] = row['symbol']
            frames.append(tickDf)
        except Exception as e:
            print(e)
            print("Cannot read file: ", row["filename"])
    del emaster_index_df

    # Get Index data from Fdata
    FDATA_INDEX_DIR = "D:\\fdata_ami\\MetaStock\\EOD\\Chi so"
    emaster_index_df = metastock_read_master(FDATA_INDEX_DIR)
    for _, row in emaster_index_df.iterrows():
        try:
            # skip specific index symbols
            if row["symbol"] in ["VNINDEX", "VN30"]:
                continue
            print("Processing " , row["symbol"], " ...")
            tickDf = metastock_read(row["filename"], extra_buffer=50)
            tickDf = _apply_cutoff(tickDf)
            tickDf['symbol'] = row['symbol']
            frames.append(tickDf)
        except Exception as e:
            print(e)
            print("Cannot read file: ", row["filename"])
    del emaster_index_df

    if not frames:
        return pd.DataFrame()
    all_symbol_ticker_df = pd.concat(frames, ignore_index=True)
    del frames
    gc.collect()

    all_symbol_ticker_df['volume'] = all_symbol_ticker_df['volume'].astype('float64')
    return all_symbol_ticker_df

@flow(log_prints=True)
def sync_ticker_delta_table_pipeline(
    destination: str = "s3://delta-table-storage/stocks",
    full_refresh: bool = False,
) -> None:
    """Flow: ETL for syncing tickers"""

    # Task 1: Collect data from MetaStock files
    df = convert_metastock_to_df(full_refresh=full_refresh)

    # Task 2: Sync data to Delta table — release df immediately after so merge
    # buffers don't overlap with the original frame in memory.
    sync_to_delta_table(df=df, destination=destination)
    del df
    gc.collect()

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
