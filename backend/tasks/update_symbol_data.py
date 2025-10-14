from pathlib import Path
from prefect import flow
from deltalake import DeltaTable
import os 
import pandas as pd
from custom_metastock2pd import metastock_read, metastock_read_master

@flow(log_prints=True)
def update_symbol_data(symbol: str):
    """Update symbol data"""
    print(f"Updating symbol data for {symbol}")

    DNSE_STOCK_DIR = "D:\\dnse\\eod\\stock"


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
        .when_matched_update_all(predicate="target.key == source.key " \
            "AND target.volume != source.volume AND target.close != source.close")\
        .execute()
    print(result)

    return result


# Run the flow
if __name__ == "__main__":

    update_symbol_data.from_source(
        source=str(Path(__file__).parent),  # code stored in local directory
        entrypoint="update_symbol_data.py:update_symbol_data",
    ).deploy(
        name="update-symbol-data",
        work_pool_name="my-worker",
    )
