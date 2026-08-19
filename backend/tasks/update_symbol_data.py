import gc
from pathlib import Path
from prefect import flow
from deltalake import DeltaTable
from clickhouse_driver import Client  # type: ignore
import os
import pandas as pd
from custom_metastock2pd import metastock_read, metastock_read_master

# Reuse the yields/macro + Delta + ClickHouse plumbing already defined for the
# OHLC crawl so backfills go through the exact same pipeline (Delta merge → CDF
# → ClickHouse) instead of a divergent code path.
from crawl_ohlc_data import (
    crawl_wichart_macro,
    sync_to_delta_table,
    sync_delta_cdf_to_clickhouse,
)

@flow(log_prints=True)
def update_symbol_data(symbol: str):
    """Update symbol data"""
    print(f"Updating symbol data for {symbol}")

    # DNSE_STOCK_DIR = "D:\\dnse\\eod\\stock"
    DNSE_STOCK_DIR = "D:\\fdata_ami\\MetaStock\\EOD\\Co phieu"


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

    emaster_df = get_df_emaster(DNSE_STOCK_DIR)
    df = emaster_df[emaster_df['symbol'] == symbol]

    file_name = df['filename'].iloc[0]
    tick_df = metastock_read(file_name, extra_buffer=50)
    tick_df = tick_df.sort_index().reset_index(names='date')
    tick_df['symbol'] = symbol
    tick_df['key'] = tick_df['symbol'] + '_' + tick_df['date'].astype(str)

    df = tick_df[['key', 'symbol', 'date', 'open', 'high', 'low', 'close', 'volume']]
    print(df.tail())

    storage_options = {
        "AWS_ACCESS_KEY_ID": "CzOwnLkEDXQy951AOqes",
        "AWS_SECRET_ACCESS_KEY": "fdRe91TOtqTl0icUkZLsUnWvZa90aZ5qG5rVEf7S",
        "AWS_ENDPOINT_URL": "http://localhost:9000",
        "AWS_ALLOW_HTTP": "true",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_REGION": 'ap-southeast-1',
        "aws_conditional_put": "etag",
    }

    dt = DeltaTable("s3://delta-table-storage/stocks", storage_options=storage_options)
    result = dt.merge(df,
            predicate="target.key == source.key",
            source_alias="source",
            target_alias="target"
    ) \
        .when_not_matched_insert_all() \
        .when_matched_update_all(predicate="target.key == source.key")\
        .execute()
    print(result)

    # Sync to ClickHouse
    ch_host     = os.getenv("CLICKHOUSE_HOST", "localhost")
    ch_port     = int(os.getenv("CLICKHOUSE_PORT", "9010"))
    ch_user     = os.getenv("CLICKHOUSE_USER", "kyostyle1")
    ch_password = os.getenv("CLICKHOUSE_PASSWORD", "kyostyle1")
    ch_database = os.getenv("CLICKHOUSE_DB", "default")
    ch_table    = os.getenv("CLICKHOUSE_OHLC_EOD_TABLE", "ohlc_eod")

    ch_client = Client(host=ch_host, port=ch_port, user=ch_user, password=ch_password, database=ch_database)

    ch_df = df[["date", "symbol", "open", "high", "low", "close", "volume"]].copy()
    ch_df["date"] = pd.to_datetime(ch_df["date"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        ch_df[col] = pd.to_numeric(ch_df[col], errors="coerce")
    ch_df = ch_df.dropna()

    rows = [
        (pd.to_datetime(r[0]).date(), str(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[6]))
        for r in ch_df.itertuples(index=False, name=None)
    ]
    if rows:
        ch_client.execute(
            f"INSERT INTO {ch_database}.{ch_table} (date, symbol, open, high, low, close, volume) VALUES",
            rows,
            types_check=False,
        )
        print(f"ClickHouse: inserted {len(rows)} rows for {symbol}")

    return result


@flow(log_prints=True)
def backfill_yields(
    destination: str = "s3://delta-table-storage/stocks",
    full_refresh: bool = True,
    max_days: int = 20,
) -> None:
    """Backfill wichart bond-yield series through the OHLC pipeline.

    Mirrors ``sync_ticker_delta_table_pipeline`` but only carries the macro
    (yield) pseudo-symbols — no MetaStock ticker load — so it can be run on
    demand to (re)load yields without touching the stock/index bars.

    ``full_refresh=True`` (default) loads the full yield history back to 2008;
    set it to ``False`` to only pick up the last ``max_days`` of values.
    """
    macro_df = crawl_wichart_macro(full_refresh=full_refresh, max_days=max_days)
    if macro_df.empty:
        print("No yield rows to backfill — nothing to do.")
        return

    # Task 2: Merge yields into the Delta table (same table/partitioning as OHLC).
    sync_to_delta_table(df=macro_df, destination=destination)
    del macro_df
    gc.collect()

    # Task 3: Propagate only the new Delta CDF changes into ClickHouse.
    sync_delta_cdf_to_clickhouse(destination=destination)


# Run the flow
if __name__ == "__main__":

    update_symbol_data.from_source(
        source=str(Path(__file__).parent),  # code stored in local directory
        entrypoint="update_symbol_data.py:update_symbol_data",
    ).deploy(
        name="update-symbol-data",
        work_pool_name="my-worker",
    )

    backfill_yields.from_source(
        source=str(Path(__file__).parent),  # code stored in local directory
        entrypoint="update_symbol_data.py:backfill_yields",
    ).deploy(
        name="backfill-yields",
        work_pool_name="my-worker",
    )
