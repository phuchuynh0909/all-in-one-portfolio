from typing import Any
from pathlib import Path
import gc
import sys
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

# Set up the Python path so `app.*` resolves when this runs as a script.
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.utils.wichart import fetchMacroFrame, fetchSectorFrame, sectorSymbol
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
    port = int(_get_env("CLICKHOUSE_PORT", "9010"))   # native TCP port (not HTTP 8123)
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

def _arrow_to_pandas(arrow_obj: Any) -> pd.DataFrame:
    """Arrow → pandas, whichever Arrow the installed deltalake hands back.

    deltalake 0.x returns pyarrow objects; 1.x returns arro3 ones, which have no
    ``to_pandas`` but do export the Arrow PyCapsule interface, so ``pa.table``
    adopts them zero-copy. requirements.txt floats the version, so support both.
    """
    if arrow_obj is None:
        return pd.DataFrame()
    to_pandas_fn = getattr(arrow_obj, "to_pandas", None)
    if callable(to_pandas_fn):
        return pd.DataFrame(to_pandas_fn())

    import pyarrow as pa

    return pa.table(arrow_obj).to_pandas()


def _delta_table_to_dataframe(dt: DeltaTable) -> pd.DataFrame:
    return _arrow_to_pandas(dt.to_pyarrow_table())

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


def _delta_merge_update_predicate_sql() -> str:
    """Predicate for when_matched_update_all.

    Strict ``!=`` on float columns is almost never false after Parquet/Delta round-trip, so the
    same run keeps updating thousands of rows. Only ``open/high/low/close`` are rounded at load;
    ``volume`` is especially noisy. Use abs-diff thresholds (override via env).
    """
    eps_c = float(_get_env("DELTA_MERGE_CLOSE_EPSILON", "1e-4"))
    eps_v = float(_get_env("DELTA_MERGE_VOLUME_EPSILON", "1.0"))
    return (
        f"(abs(target.close - source.close) > {eps_c}) OR "
        f"(abs(target.volume - source.volume) > {eps_v})"
    )


@task
def sync_to_delta_table(df: pd.DataFrame, destination = "s3://delta-table-storage/stocks") -> None:
    """Merge OHLC upserts into Delta.

    Matched-row updates use ``_delta_merge_update_predicate_sql()`` (epsilon compares) so
    repeat runs do not rewrite rows for float bit noise. Tune ``DELTA_MERGE_CLOSE_EPSILON`` /
    ``DELTA_MERGE_VOLUME_EPSILON``.

    Reload ``DeltaTable`` each year and dedupe source on ``key`` — reusing one handle after
    ``execute()`` can insert duplicate keys and inflate merge metrics across runs.

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
            # Reload after each execute() — stale handles cause duplicate-key inserts on later merges.
            dt = DeltaTable(destination, storage_options=storage_options)
            year_df = df.loc[df["year"] == year].copy()
            n_before = len(year_df)
            year_df = year_df.drop_duplicates(subset=["key"], keep="last")
            if len(year_df) < n_before:
                print(f"  {year}: dropped {n_before - len(year_df)} duplicate key row(s) in source")

            result = (
                dt.merge(
                    year_df,
                    predicate=f"target.key == source.key AND target.year = '{year}'",
                    source_alias="source",
                    target_alias="target",
                )
                .when_not_matched_insert_all()
                .when_matched_update_all(predicate=_delta_merge_update_predicate_sql())
                .execute()
            )
            print(f"  {year}: {result}")
            del year_df, result, dt
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

def _full_load_snapshot(dt: DeltaTable) -> int:
    snapshot_df = _delta_table_to_dataframe(dt)
    normalized_snapshot = _normalize_ohlc_df(snapshot_df)
    del snapshot_df
    inserted = _insert_ohlc_df_to_clickhouse(normalized_snapshot)
    del normalized_snapshot
    gc.collect()
    return inserted


@task(log_prints=True)
def sync_delta_cdf_to_clickhouse(
    destination: str = "s3://delta-table-storage/stocks",
    state_path: str = SYNC_STATE_PATH,
) -> int:
    storage_options = _get_delta_storage_options()
    dt = DeltaTable(destination, storage_options=storage_options)

    state = _load_sync_state(state_path)
    last_synced_version = state.get("last_synced_version")
    full_load_on_first_run = _env_flag("DELTA_CDF_FULL_LOAD_ON_FIRST_RUN", "false")

    metadata = dt.metadata()
    cdf_enabled = str(metadata.configuration.get("delta.enableChangeDataFeed", "false")).lower() == "true"
    if not cdf_enabled:
        # Turning CDF on only records changes from the *next* commit, so a table
        # written without it has no history to replay. Take the snapshot first if
        # the operator asked for a first-run full load — otherwise everything
        # already in the table would never reach ClickHouse.
        inserted = 0
        if last_synced_version is None and full_load_on_first_run:
            inserted = _full_load_snapshot(dt)
            print(f"CDF was off — full load inserted {inserted} rows.")
        else:
            print(
                "CDF not enabled — enabling now and bookmarking current version. Rows already "
                "in the table stay unsynced; set DELTA_CDF_FULL_LOAD_ON_FIRST_RUN=true to backfill."
            )
        dt.alter.set_table_properties({"delta.enableChangeDataFeed": "true"})
        # set_table_properties commits, so re-read the version to bookmark the
        # commit we just made rather than the one before it.
        dt = DeltaTable(destination, storage_options=storage_options)
        _save_sync_state(state_path, {"last_synced_version": int(dt.version())})
        return inserted

    latest_version = dt.version()

    if last_synced_version is None and full_load_on_first_run:
        inserted = _full_load_snapshot(dt)
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
    cdf_df = _arrow_to_pandas(cdf_arrow)
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
def crawl_wichart_macro(full_refresh: bool = False, max_days: int = 20) -> pd.DataFrame:
    """Wichart bond yields, shaped as OHLC rows so they ride the ticker pipeline.

    Each series becomes a pseudo-symbol (``VIETNAM_5Y``), the same way the index
    series already share this table. One value per day, so the bar is flat —
    open=high=low=close=yield — with zero volume. Note the unit: these are
    percent, not VND, so anything that aggregates across symbols has to exclude
    them.

    ``max_days`` mirrors the MetaStock cutoff in ``convert_metastock_to_df``: the
    endpoint serves the full history on every call, and appending ~17.5k rows to
    an incremental run would dwarf the ~20 days of ticker data it carries. Run
    with ``full_refresh=True`` to load the history back to 2008.
    """
    df = fetchMacroFrame()
    if df.empty:
        print("No macro rows crawled")
        return pd.DataFrame()

    if not full_refresh:
        cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=max_days)
        df = df[df["date"] >= cutoff]

    value = df["value"].astype("float64")
    macro_df = pd.DataFrame({
        "date": pd.to_datetime(df["date"]),
        "symbol": df["dim_name"].str.upper(),
        "open": value,
        "high": value,
        "low": value,
        "close": value,
        "volume": 0.0,
    }).reset_index(drop=True)

    print(
        f"Macro: {len(macro_df):,} rows as {sorted(macro_df['symbol'].unique())}"
        + ("" if full_refresh else f" (since {cutoff.date()})")
    )
    return macro_df


# Level 3 has few enough sectors to take whole; level 4 has 157, most of them
# too small to matter, so it is capped by market cap the way the standalone
# wichart_sector crawler does.
SECTOR_LEVELS = (3, 4)
SECTOR_LEVEL_4_MIN_MARKET_CAP = float(_get_env("WICHART_SECTOR_L4_MIN_VONHOA", "10000"))
SECTOR_HISTORY_START = _get_env("WICHART_SECTOR_HISTORY_START", "2020-01-01")


def _sector_ids_by_level() -> dict[int, list[int]]:
    """Wichart list ids to crawl per level, from the app's ``sector`` table.

    The ids in that table *are* the wichart list ids for levels 3 and 4, so no
    mapping is needed. Level 4 is filtered by ``vonhoa_d`` to keep the request
    count near a hundred rather than two.
    """
    from app.db.base import SessionLocal
    from app.db.models.market import Sector

    ids_by_level: dict[int, list[int]] = {}
    db = SessionLocal()
    try:
        for level in SECTOR_LEVELS:
            query = db.query(Sector.id).filter(Sector.level == level)
            if level == 4:
                query = query.filter(Sector.vonhoa_d > SECTOR_LEVEL_4_MIN_MARKET_CAP)
            ids_by_level[level] = [row[0] for row in query.all()]
    finally:
        db.close()
    return ids_by_level


@task(log_prints=True)
def crawl_wichart_sectors(full_refresh: bool = False, max_days: int = 252) -> pd.DataFrame:
    """Wichart sector indices, shaped as OHLC rows so they ride the ticker pipeline.

    Same trick as ``crawl_wichart_macro``: each sector becomes a pseudo-symbol
    (``SECTOR3_26``, see ``sectorSymbol``) with a flat bar —
    open=high=low=close=index level — and zero volume. Levels 1 and 2 already
    arrive as ``0001``/``0500`` from the MetaStock index files; this covers 3 and
    4, which have no MetaStock source at all.

    Unlike the macro endpoint, this one honours ``from``/``to``, so an
    incremental run asks for ``max_days`` back instead of fetching the whole
    history and slicing. Note the unit: these are index levels, not VND, so
    anything aggregating across symbols has to exclude them — the watchlist in
    ``_load_delta_stocks`` already does.
    """
    to_date = pd.Timestamp.today().normalize()
    from_date = (
        pd.Timestamp(SECTOR_HISTORY_START)
        if full_refresh
        else to_date - pd.Timedelta(days=max_days)
    )

    try:
        ids_by_level = _sector_ids_by_level()
    except Exception as exc:
        # The crawler box may not reach MySQL. Skipping costs today's sector rows;
        # failing here would cost the whole ticker run.
        print(f"Cannot read sector ids from the database, skipping sectors: {exc}")
        return pd.DataFrame()

    requested = sum(len(v) for v in ids_by_level.values())
    if not requested:
        print("No sectors to crawl")
        return pd.DataFrame()
    print(
        f"Sectors: requesting {requested} series "
        f"({', '.join(f'L{k}={len(v)}' for k, v in ids_by_level.items())}) "
        f"from {from_date.date()} to {to_date.date()}"
    )

    df = fetchSectorFrame(
        ids_by_level,
        from_date=from_date.strftime("%Y-%m-%d"),
        to_date=to_date.strftime("%Y-%m-%d"),
    )
    if df.empty:
        print("No sector rows crawled")
        return pd.DataFrame()

    value = df["value"].astype("float64")
    sector_df = pd.DataFrame({
        "date": pd.to_datetime(df["date"]),
        "symbol": df["symbol"],
        "open": value,
        "high": value,
        "low": value,
        "close": value,
        "volume": 0.0,
    }).reset_index(drop=True)

    print(f"Sectors: {len(sector_df):,} rows as {sector_df['symbol'].nunique()} pseudo-symbols")
    return sector_df


@task(log_prints=True)
def sync_to_clickhouse(df: pd.DataFrame) -> int:
    normalized = _normalize_ohlc_df(df)
    inserted = _insert_ohlc_df_to_clickhouse(normalized)
    database = _get_env("CLICKHOUSE_DB", "default")
    table = _get_env("CLICKHOUSE_OHLC_EOD_TABLE", "ohlc_eod")
    print(f"Inserted {inserted} rows into ClickHouse table {database}.{table}")
    return inserted

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
    for col in ('open', 'high', 'low', 'close'):
        if col in all_symbol_ticker_df.columns:
            all_symbol_ticker_df[col] = pd.to_numeric(
                all_symbol_ticker_df[col], errors='coerce'
            ).round(2)
    return all_symbol_ticker_df

@task(log_prints=True)
def build_level5_indices() -> int:
    """Derive the level-5 sector indices from constituents and load ClickHouse.

    Runs last and reads back from ClickHouse rather than riding the Delta merge:
    the index chains constituent returns over the whole history, so it needs the
    rows this flow has just synced, not the ~20 days in memory. The whole series
    is recomputed each run — 24 sectors times ~1700 bars is nothing, and
    ReplacingMergeTree makes the re-insert idempotent.
    """
    from app.services.stock_service import build_level5_sector_index

    df = build_level5_sector_index()
    if df.empty:
        print("No level 5 indices built")
        return 0

    inserted = _insert_ohlc_df_to_clickhouse(_normalize_ohlc_df(df))
    print(f"Level 5: inserted {inserted} index rows into ClickHouse")
    return inserted


@flow(log_prints=True)
def sync_ticker_delta_table_pipeline(
    destination: str = "s3://delta-table-storage/stocks",
    full_refresh: bool = False,
) -> None:
    """Flow: ETL for syncing tickers"""

    # Task 1: Collect data from MetaStock files
    df = convert_metastock_to_df(full_refresh=full_refresh)

    # Task 1b: Append the wichart macro series (bond yields) as pseudo-symbols.
    # Appended after the OHLC rounding in Task 1 on purpose — yields carry three
    # decimals and rounding to 2 would flatten them.
    macro_df = crawl_wichart_macro(full_refresh=full_refresh)
    if not macro_df.empty:
        df = pd.concat([df, macro_df], ignore_index=True)
    del macro_df
    gc.collect()

    # Task 1c: Append the wichart sector indices (levels 3 and 4) as
    # pseudo-symbols. Also after the rounding in Task 1 — index levels carry
    # more than two decimals and the merge compares close with an epsilon.
    sector_df = crawl_wichart_sectors(full_refresh=full_refresh)
    if not sector_df.empty:
        df = pd.concat([df, sector_df], ignore_index=True)
    del sector_df
    gc.collect()

    # Task 2: Sync data to Delta table — release df immediately after so merge
    # buffers don't overlap with the original frame in memory.
    sync_to_delta_table(df=df, destination=destination)
    del df
    gc.collect()

    # Task 3: Sync only Delta CDF changes to ClickHouse
    sync_delta_cdf_to_clickhouse(destination=destination)

    # Task 4: Derive the level-5 sector indices from the constituents that just
    # landed. Must follow Task 3 — it reads them back out of ClickHouse.
    build_level5_indices()


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
