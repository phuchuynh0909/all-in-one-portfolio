"""Plain-Python entry point — same logic as crawl_ohlc_data.py without Prefect.

Run:
    python sync_ohlc.py
    python sync_ohlc.py --destination s3://delta-table-storage/stocks
"""

from typing import Any
import gc
import argparse
import os
import csv
import json
from itertools import chain

import pandas as pd
from custom_metastock2pd import metastock_read, metastock_read_master, metastock_emaster
from deltalake import DeltaTable
from clickhouse_driver import Client  # type: ignore

SYNC_STATE_PATH = os.getenv("DELTA_CDF_SYNC_STATE_PATH", "./.state/ohlc_delta_cdf_sync_state.json")


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_env(name: str, default: str) -> str:
    return os.getenv(name, default)

def _env_flag(name: str, default: str = "false") -> bool:
    return _get_env(name, default).lower() in {"1", "true", "yes", "y"}

def _get_delta_storage_options() -> dict[str, str]:
    return {
        "AWS_ACCESS_KEY_ID":        "CzOwnLkEDXQy951AOqes",
        "AWS_SECRET_ACCESS_KEY":    "fdRe91TOtqTl0icUkZLsUnWvZa90aZ5qG5rVEf7S",
        "AWS_ENDPOINT_URL":         "http://localhost:9000",
        "AWS_ALLOW_HTTP":           "true",
        "AWS_EC2_METADATA_DISABLED":"true",
        "AWS_REGION":               "ap-southeast-1",
        "aws_conditional_put":      "etag",
    }

def _get_ch_client() -> Client:
    return Client(
        host     = _get_env("CLICKHOUSE_HOST",     "localhost"),
        port     = int(_get_env("CLICKHOUSE_PORT", "9010")),
        user     = _get_env("CLICKHOUSE_USER",     "kyostyle1"),
        password = _get_env("CLICKHOUSE_PASSWORD", "kyostyle1"),
        database = _get_env("CLICKHOUSE_DB",       "default"),
    )

def _load_sync_state(state_path: str) -> dict[str, Any]:
    if not os.path.exists(state_path):
        return {}
    with open(state_path, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_sync_state(state_path: str, state: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f)

def _normalize_ohlc_df(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy()
    try:
        normalized["date"] = pd.to_datetime(normalized["date"], format="%Y%m%d")
    except ValueError:
        normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        normalized[col] = pd.to_numeric(normalized[col], errors="coerce")
    normalized = normalized.dropna(
        subset=["date", "symbol", "open", "high", "low", "close", "volume"]
    ).copy()
    return normalized[["date", "symbol", "open", "high", "low", "close", "volume"]].copy()

def _normalize_cdf_to_ohlc_df(cdf_df: pd.DataFrame) -> pd.DataFrame:
    if cdf_df.empty:
        return cdf_df
    if "_change_type" in cdf_df.columns:
        cdf_df = cdf_df[cdf_df["_change_type"].isin(["insert", "update_postimage"])]
    required_cols = ["date", "symbol", "open", "high", "low", "close", "volume"]
    missing = [c for c in required_cols if c not in cdf_df.columns]
    if missing:
        raise ValueError(f"CDF output missing required columns: {missing}")
    normalized = cdf_df[required_cols].copy()
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        normalized[col] = pd.to_numeric(normalized[col], errors="coerce")
    return normalized.dropna(subset=required_cols)

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

def _insert_ohlc_df_to_clickhouse(df: pd.DataFrame, batch_size: int = 50000) -> int:
    database = _get_env("CLICKHOUSE_DB", "default")
    table    = _get_env("CLICKHOUSE_OHLC_EOD_TABLE", "ohlc_eod")
    client   = _get_ch_client()
    _ensure_ohlc_table_exists(client, database, table)

    inserted = 0
    for start in range(0, len(df), batch_size):
        chunk = df.iloc[start : start + batch_size]
        rows = [
            (
                pd.to_datetime(r[0]).date(),
                str(r[1]),
                float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[6]),
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
    fn = getattr(arrow_table, "to_pandas", None)
    return pd.DataFrame(fn() if callable(fn) else arrow_table)


# ── pipeline steps ────────────────────────────────────────────────────────────

def convert_metastock_to_df() -> pd.DataFrame:
    max_days = 20

    with open(r"D:\Projects\trading_toolbox\watchlist.csv", "r") as f:
        watchlist: list[str] = list(chain.from_iterable(csv.reader(f)))

    frames: list[pd.DataFrame] = []

    # DNSE stocks
    DNSE_STOCK_DIR = r"D:\dnse\eod\stock"
    emaster_df = _get_df_emaster(DNSE_STOCK_DIR)
    df = emaster_df.query("symbol in @watchlist")
    del emaster_df
    for _, row in df.iterrows():
        print("Processing", row["symbol"], "…")
        filename = row["filename"]
        if isinstance(filename, pd.Series):
            filename = filename.iloc[0]
        try:
            tick = metastock_read(filename, extra_buffer=50)
            tick = tick.sort_index().tail(max_days).reset_index(names="date")
            tick["symbol"] = row["symbol"]
            frames.append(tick)
        except Exception as e:
            print(e, "| Cannot read:", filename)
    del df

    # DNSE index
    DNSE_INDEX_DIR = r"D:\dnse\eod\index"
    emaster_idx = metastock_emaster(DNSE_INDEX_DIR)
    for _, row in emaster_idx.iterrows():
        try:
            print("Processing", row["symbol"], "…")
            tick = metastock_read(row["filename"], extra_buffer=50)
            tick = tick.sort_index().tail(50).reset_index(names="date")
            tick["symbol"] = row["symbol"]
            frames.append(tick)
        except Exception as e:
            print(e, "| Cannot read:", row["filename"])
    del emaster_idx

    # Fdata index
    FDATA_INDEX_DIR = r"D:\fdata_ami\MetaStock\EOD\Chi so"
    emaster_fdata = metastock_read_master(FDATA_INDEX_DIR)
    for _, row in emaster_fdata.iterrows():
        if row["symbol"] in ("VNINDEX", "VN30"):
            continue
        try:
            print("Processing", row["symbol"], "…")
            tick = metastock_read(row["filename"], extra_buffer=50)
            tick = tick.sort_index().tail(50).reset_index(names="date")
            tick["symbol"] = row["symbol"]
            frames.append(tick)
        except Exception as e:
            print(e, "| Cannot read:", row["filename"])
    del emaster_fdata

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    del frames
    gc.collect()
    result["volume"] = result["volume"].astype("float64")
    return result


def sync_to_delta_table(df: pd.DataFrame, destination: str) -> None:
    """Merge OHLC upserts into a month-partitioned Delta table.

    Merges one month at a time. Because the table is partitioned by `month`,
    Delta resolves each merge predicate (`target.month = '{month}'`) via the
    transaction log — only that month's partition files are loaded, not the
    full table. This eliminates the OOM that occurred with an unpartitioned table.

    Requires the table to be partitioned by `month` (run migrate_to_partitioned.py
    once to convert an existing unpartitioned table).
    """
    try:
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    except ValueError:
        df["date"] = pd.to_datetime(df["date"])

    df["key"]   = df["symbol"] + "_" + df["date"].dt.strftime("%Y-%m-%d")
    df["month"] = df["date"].dt.to_period("M").astype(str)   # e.g. "2026-04"
    df = df[["key", "symbol", "date", "month", "open", "high", "low", "close", "volume"]]

    storage_options = _get_delta_storage_options()
    dt = DeltaTable(destination, storage_options=storage_options)

    months = sorted(df["month"].unique())
    print(f"Merging {len(months)} month(s) into Delta …")

    for month in months:
        month_df = df[df["month"] == month].copy()

        result = (
            dt.merge(
                month_df,
                # Partition predicate prunes all files outside this month's
                # partition directory — only ~N_symbols rows are loaded.
                predicate=f"target.key == source.key AND target.month = '{month}'",
                source_alias="source",
                target_alias="target",
            )
            .when_not_matched_insert_all()
            .when_matched_update_all(
                predicate="target.volume != source.volume OR target.close != source.close"
            )
            .execute()
        )
        print(f"  {month}: {result}")
        del month_df, result
        gc.collect()

    del df
    gc.collect()

    if _env_flag("DELTA_SYNC_RUN_VACUUM"):
        print(dt.vacuum(retention_hours=24, dry_run=False, enforce_retention_duration=False))
        gc.collect()

    if _env_flag("DELTA_SYNC_RUN_OPTIMIZE"):
        print(dt.optimize.compact())
        gc.collect()


def sync_delta_cdf_to_clickhouse(
    destination: str,
    state_path: str = SYNC_STATE_PATH,
) -> int:
    dt = DeltaTable(destination, storage_options=_get_delta_storage_options())

    cdf_enabled = (
        str(dt.metadata().configuration.get("delta.enableChangeDataFeed", "false")).lower()
        == "true"
    )
    if not cdf_enabled:
        raise RuntimeError(
            "Delta CDF not enabled. Set 'delta.enableChangeDataFeed=true' on the table first."
        )

    latest_version     = dt.version()
    state              = _load_sync_state(state_path)
    last_synced        = state.get("last_synced_version")
    full_load_first    = _env_flag("DELTA_CDF_FULL_LOAD_ON_FIRST_RUN")

    if last_synced is None and full_load_first:
        snapshot    = _delta_table_to_dataframe(dt)
        normalized  = _normalize_ohlc_df(snapshot)
        del snapshot
        inserted = _insert_ohlc_df_to_clickhouse(normalized)
        del normalized
        gc.collect()
        _save_sync_state(state_path, {"last_synced_version": int(latest_version)})
        print(f"Full load: inserted {inserted} rows, version={latest_version}")
        return inserted

    start_version = int(last_synced) + 1 if last_synced is not None else int(latest_version)
    if start_version > int(latest_version):
        print(f"Nothing to sync. last={last_synced}, latest={latest_version}")
        return 0

    cdf_reader = dt.load_cdf(starting_version=start_version, ending_version=int(latest_version))
    cdf_arrow  = cdf_reader.read_all()
    if cdf_arrow is None:
        cdf_df = pd.DataFrame()
    else:
        fn     = getattr(cdf_arrow, "to_pandas", None)
        cdf_df = pd.DataFrame(fn() if callable(fn) else cdf_arrow)
    del cdf_arrow, cdf_reader

    normalized = _normalize_cdf_to_ohlc_df(cdf_df)
    del cdf_df

    if normalized.empty:
        _save_sync_state(state_path, {"last_synced_version": int(latest_version)})
        print(f"No CDF rows for versions {start_version}..{latest_version}")
        return 0

    inserted = _insert_ohlc_df_to_clickhouse(normalized)
    del normalized
    gc.collect()

    _save_sync_state(state_path, {"last_synced_version": int(latest_version)})
    print(f"Synced versions {start_version}..{latest_version} → ClickHouse: {inserted} rows")
    return inserted


def _get_df_emaster(dir_path: str) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for entry in os.listdir(dir_path):
        folder_path = os.path.join(dir_path, entry)
        if os.path.isdir(folder_path):
            parts.append(metastock_read_master(folder_path, encoding="latin1"))
        else:
            print("Not a folder:", folder_path)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


# ── main ──────────────────────────────────────────────────────────────────────

def main(destination: str = "s3://delta-table-storage/stocks") -> None:
    print(f"=== Step 1: Read MetaStock files ===")
    df = convert_metastock_to_df()
    print(f"Loaded {len(df):,} rows")

    print(f"\n=== Step 2: Merge into Delta table ({destination}) ===")
    sync_to_delta_table(df=df, destination=destination)
    del df
    gc.collect()

    print(f"\n=== Step 3: Sync Delta CDF → ClickHouse ===")
    sync_delta_cdf_to_clickhouse(destination=destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync MetaStock OHLC data to Delta + ClickHouse")
    parser.add_argument(
        "--destination",
        default="s3://delta-table-storage/stocks",
        help="Delta table S3 path",
    )
    args = parser.parse_args()
    main(destination=args.destination)
