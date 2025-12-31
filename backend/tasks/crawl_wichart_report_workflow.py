import os
import sys
from prefect import flow, task
import pyarrow as pa
from pathlib import Path
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
from app.utils.wichart import decrypt

import asyncio
from playwright.async_api import async_playwright

WICHART_EMAIL = os.getenv("WICHART_EMAIL", "kyostyle1@gmail.com")
WICHART_PASSWORD = os.getenv("WICHART_PASSWORD", "kyostyle1")
STORAGE_PATH = os.path.join(CURRENT_DIR, "wichart_storage.json")

# Define the PyArrow schema for WichartReport
wichart_report_schema = pa.schema([
    pa.field('id', pa.int64(), nullable=False),
    pa.field('mack', pa.string(), nullable=True),
    pa.field('tenbaocao', pa.string(), nullable=True),
    pa.field('nguon', pa.string(), nullable=True),
    pa.field('khuyennghi', pa.string(), nullable=True),
    pa.field('giamuctieu', pa.float64(), nullable=True),
    pa.field('giamuctieu_dieuchinh', pa.float64(), nullable=True),
    pa.field('upside_hientai', pa.float64(), nullable=True),
    pa.field('lnst_duphong', pa.float64(), nullable=True),
    pa.field('tt_lnst_duphong_yoy', pa.float64(), nullable=True),
    pa.field('pe_mack_n0', pa.float64(), nullable=True),
    pa.field('lnst_duphong_pt', pa.float64(), nullable=True),
    pa.field('ngaykn', pa.timestamp('us'), nullable=True),
    pa.field('ngay_congbo', pa.timestamp('us'), nullable=True),
    pa.field('rsnganh', pa.string(), nullable=True),
    pa.field('idnganh', pa.int64(), nullable=True),
    pa.field('idnganhcap3', pa.int64(), nullable=True),
    pa.field('tennganhcap3', pa.string(), nullable=True),
    pa.field('kibaocao', pa.string(), nullable=True),
    pa.field('loaibaocao', pa.string(), nullable=True),
    pa.field('url', pa.string(), nullable=True)
])


async def crawl_reports_via_browser() -> pd.DataFrame:
    """Crawl reports by navigating to widata.vn and clicking on tabs."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Navigate to the page
        print("Navigating to widata.vn/bao-cao-phan-tich...")
        await page.goto("https://widata.vn/bao-cao-phan-tich", wait_until="networkidle")
        
        # Define tabs to click (using MUI Typography h2 elements)
        tabs = [
            {"name": "Ngành", "selector": "h2.MuiTypography-subtitle2:has-text('Ngành')"},
            {"name": "Vĩ mô", "selector": "h2.MuiTypography-subtitle2:has-text('Vĩ mô')"},
            {"name": "Báo cáo chiến lược", "selector": "h2.MuiTypography-subtitle2:has-text('chiến lược')"},
            {"name": "Doanh nghiệp", "selector": "h2.MuiTypography-subtitle2:has-text('Doanh nghiệp')"},
        ]

        result_df = pd.DataFrame()

        for tab in tabs:
            try:
                print(f"\nClicking on tab: {tab['name']}")
                
                # Wait for the API response when clicking the tab
                async with page.expect_response(
                    lambda resp: "wichartapi" in resp.url and "report" in resp.url,
                    timeout=15000
                ) as response_info:
                    await page.click(tab["selector"])
                
                response = await response_info.value
                body = await response.json()
                enc = body.get('enc')

                if enc:
                    decrypted_data = decrypt(enc)
                    data = json.loads(decrypted_data)
                    
                    reports = data.get('result', [])
                    if reports:
                        tab_df = pd.DataFrame(reports)
                        tab_df['tab_source'] = tab['name']
                        result_df = pd.concat([result_df, tab_df], ignore_index=True)
                    
                    print(f"Tab '{tab['name']}' response: {len(reports)} items")
                else:
                    print(f"Tab '{tab['name']}': No encrypted data found")
                
                # Small delay between tabs
                await asyncio.sleep(1)
                
            except Exception as e:
                print(f"Error clicking tab '{tab['name']}': {e}")

        await browser.close()
        
        print(f"\nTotal reports crawled: {len(result_df)}")
        return result_df


@task(log_prints=True)
def crawl_wichart_report() -> pd.DataFrame:
    """Task: Crawl Wichart reports via browser automation."""
    print("Starting browser-based report crawling...")
    result_df = asyncio.run(crawl_reports_via_browser())
    return result_df


@task(log_prints=True)
def save_to_delta_table(df: pd.DataFrame):
    """Task: Save crawled reports to Delta Lake."""
    if df.empty:
        print("No data to save")
        return
    
    tablePath = "s3://delta-table-storage/raw_wichart_report"
    storage_options = {
        "AWS_ACCESS_KEY_ID": os.getenv("MINIO_ACCESS_KEY"),
        "AWS_SECRET_ACCESS_KEY": os.getenv("MINIO_SECRET_KEY"),
        "AWS_ENDPOINT_URL": f"http://{os.getenv('MINIO_ENDPOINT')}",
        "AWS_ALLOW_HTTP": "true",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_REGION": 'us-east-1',
        "aws_conditional_put": "etag",
    }

    # Convert date columns to datetime format
    date_columns = ['ngaykn', 'ngay_congbo']
    for col in date_columns:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce')

    # Remove tab_source column if exists (not in schema)
    if 'tab_source' in df.columns:
        df = df.drop(columns=['tab_source'])

    # Keep only columns that exist in the schema
    schema_columns = [field.name for field in wichart_report_schema]
    existing_columns = [col for col in schema_columns if col in df.columns]
    df = df[existing_columns]

    # Convert DataFrame to PyArrow Table using the schema
    try:
        pyarrow_table = pa.Table.from_pandas(df, schema=wichart_report_schema, preserve_index=False)
    except Exception as e:
        print(f"Error converting to PyArrow table with schema: {e}")
        # Fallback: let PyArrow infer schema
        pyarrow_table = pa.Table.from_pandas(df, preserve_index=False)

    isExist = DeltaTable.is_deltatable(tablePath, storage_options=storage_options)
    
    if isExist:
        dt = DeltaTable(tablePath, storage_options=storage_options)
        result = dt.merge(
            df, 
            predicate="target.id = source.id", 
            source_alias="source", 
            target_alias="target"
        ).when_matched_update_all().when_not_matched_insert_all().execute()
        print(f"Merged {len(df)} records to Delta table")
    else:
        result = write_deltalake(tablePath, pyarrow_table, storage_options=storage_options, mode="overwrite")
        print(f"Created new Delta table with {len(df)} records")

    return


@flow(log_prints=True)
def crawl_wichart_report_workflow():
    """Flow: Crawl Wichart reports and save to Delta Lake."""
    
    # Task 1: Crawl reports via browser
    df = crawl_wichart_report()
    
    if df.empty:
        print("No data crawled, exiting workflow")
        return

    # Task 2: Save to Delta Table
    save_to_delta_table(df)
    
    print("Workflow completed successfully!")


# Run the flow
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--deploy", action="store_true", help="Deploy the flow instead of running it")
    parser.add_argument("--run", action="store_true", help="Run the flow locally")
    args = parser.parse_args()

    if args.deploy:
        crawl_wichart_report_workflow.from_source(
            source=str(Path(__file__).parent),
            entrypoint="crawl_wichart_report_workflow.py:crawl_wichart_report_workflow",
        ).deploy(
            name="crawl_wichart_report_workflow",
            work_pool_name="my-worker",
            # Run at 9:00 AM and 3:00 PM Vietnam time (UTC+7) from Monday to Friday
            cron="0 2,8 * * 1-5",  # UTC+0
        )
    else:
        # Default: run the workflow
        crawl_wichart_report_workflow()
