import os
import sys
from prefect import flow, task
from pathlib import Path
from deltalake import DeltaTable, write_deltalake
from dotenv import load_dotenv
load_dotenv()

# Set up the Python path first
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Now import after path is set up
# NOTE: `crawl_ohlc_data.py:sync_wichart_macro_pipeline` crawls the same series
# and also syncs them on to ClickHouse. This flow is the Delta-only predecessor;
# keep only one of the two scheduled.
from app.utils.wichart import fetchMacroFrame


@task(log_prints=True)
def crawl_macro_data():
    return fetchMacroFrame()

# def save_delta_data(df):

@flow(log_prints=True)
def crawl_wichart_workflow():
    df = crawl_macro_data()
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
