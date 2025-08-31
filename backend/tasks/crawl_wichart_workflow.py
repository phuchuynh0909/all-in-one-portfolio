import os
import sys
from prefect import flow, task
from pathlib import Path
import requests
import time
import json
import pandas as pd
from deltalake import DeltaTable, write_deltalake
from dotenv import load_dotenv
load_dotenv()

# Set up the Python path first
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Now import after path is set up
from app.utils.wichart import getNonce, getHeaders, getToken, getSign, decrypt

@task(log_prints=True)
def crawl_macro_data(token):
    url = "https://wichart.vn/wichartapi/macro/templates/data"
    
    query_params = {
        "table_name": "hst_bond",
        "time_frame": "daily",
        "value_type": "value",
        "version": "2"
    }
    fetch_dim = ["vietnam_1y", "vietnam_3y", "vietnam_5y", "vietnam_10y"]
    
    total_df = pd.DataFrame()
    for dim in fetch_dim:
        query_params['column_name'] = dim
        nonce = getNonce()
        stime = int(time.time() * 1000)
        sign_data = {
            "column_name": query_params['column_name'],
            "nonce": nonce,
            "sign-token": "ObBeWhVmYs3tP2Nz$C$FJ@P4AQfTjlPX",
            "stime": stime,
            "table_name": query_params['table_name'],
            "time_frame": query_params['time_frame'],
            "v": "v1",
            "value_type": query_params['value_type'],
            "version": query_params['version']
        }
        hashCode = getSign(sign_data)
        response = requests.get(url, params=query_params, headers=getHeaders(token, nonce, hashCode, stime))
        enc = response.json()['enc']
        data = json.loads(decrypt(enc))
        df = pd.DataFrame(
            data['data']['data'], 
            columns=['timestamp', 'value']
        )
        df['date'] = pd.to_datetime(df['timestamp'], unit='ms')
        df['dim_name'] = dim
        df['key'] = df['dim_name'] + '_' + df['date'].dt.strftime('%Y-%m-%d')

        ## remove timestamp column
        total_df = pd.concat([total_df, df])

    # filter data > 2008-01-01
    total_df = total_df[total_df['date'] >= '2008-01-01']
    total_df = total_df.drop(columns=['timestamp'])
    return total_df

# def save_delta_data(df):

@flow(log_prints=True)
def crawl_wichart_workflow():
    ## Get token
    token = getToken()

    df = crawl_macro_data(token)
    if df.empty:
        print("data is empty")
        return

    storage_options = {
        "AWS_ACCESS_KEY_ID": os.getenv("MINIO_ACCESS_KEY"),
        "AWS_SECRET_ACCESS_KEY": os.getenv("MINIO_SECRET_KEY"),
        "AWS_ENDPOINT_URL": f"http://{os.getenv('MINIO_ENDPOINT')}",
        "AWS_ALLOW_HTTP": "true",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_REGION": 'us-east-1',
        "aws_conditional_put": "etag",
    }
    table_path = "s3://delta-table-storage/wichart_macro"
    isExist = DeltaTable.is_deltatable(table_path, storage_options=storage_options)
    if isExist:
        dt = DeltaTable(table_path, storage_options=storage_options)
        result = dt.merge(                                       # target data
            source=df,                         # source data
            predicate="target.key = source.key",
            source_alias="source",
            target_alias="target"
        ).when_not_matched_insert_all().execute()
        print(result)
    else:
        result = write_deltalake(table_path, df, storage_options=storage_options)
        print(result)

# Run the flow
if __name__ == "__main__":
    # crawl_wichart_workflow()

    crawl_wichart_workflow.from_source(
        source=str(Path(__file__).parent),  # code stored in local directory
        entrypoint="crawl_wichart_workflow.py:crawl_wichart_workflow",
    ).deploy(
        name="crawl_wichart_workflow",
        work_pool_name="my-worker",
        # Run at 3:00 PM from Monday to Friday
        cron="0 8 * * 1-5", ## UTC+0
    )
