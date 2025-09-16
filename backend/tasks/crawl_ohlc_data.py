from typing import Any
from pathlib import Path
from prefect import flow, task
# from metastock2pd import metastock_read, metastock_read_master, metastock_emaster
from custom_metastock2pd import metastock_read, metastock_read_master, metastock_emaster, metastock_xmaster

import os
import pandas as pd
import csv
from itertools import chain
from os.path import isfile, join
from metastock import convert_metastock_data
from deltalake import DeltaTable
"""Flow: """
# INDEX_DIR = "D:\\fdata_ami\\MetaStock\\EOD\\Chi so"
# INDEX_DIR = "D:\\dnse\\eod\\index"
INDEX_DIR = "D:\\ami\\MetaStock\\EOD\\index"
# INDEX_DIR = "D:\\dnse\\eod\\index"
# STOCK_DIR = "D:\\dnse\\eod\\stock"
# STOCK_DIR = "D:\\fdata_ami\\MetaStock\\EOD\\Co phieu"
STOCK_DIR = "D:\\ami\\MetaStock\\EOD\\stock"
STOCK_BACKUP_DIR = "D:\\ami\\MetaStock\\EOD\\stock"

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
    df = df[["key", "symbol", "date", "open", "high", "low", "close", "volume"]]

    storage_options = {
        "AWS_ACCESS_KEY_ID": "CzOwnLkEDXQy951AOqes",
        "AWS_SECRET_ACCESS_KEY": "fdRe91TOtqTl0icUkZLsUnWvZa90aZ5qG5rVEf7S",
        "AWS_ENDPOINT_URL": "http://localhost:9000",
        "AWS_ALLOW_HTTP": "true",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_REGION": 'ap-southeast-1',
        "aws_conditional_put": "etag",
    }

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
            tickDf = tickDf.sort_index().tail(10).reset_index(names='date')
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
            tickDf = tickDf.sort_index().tail(10).reset_index(names='date')
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
            tickDf = tickDf.sort_index().tail(10).reset_index(names='date')
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

