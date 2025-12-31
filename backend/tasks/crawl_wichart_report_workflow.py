import os
import sys
from prefect import flow, task
import pyarrow as pa
import urllib.parse
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
from app.utils.wichart import getNonce, getHeaders, getSign, decrypt

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

def get_from_storage(key: str, storage_path: str = STORAGE_PATH) -> str | None:
    try:
        with open(storage_path, 'r') as f:
            storage = json.load(f)
        
        # Find wtoken in cookies
        for cookie in storage.get("cookies", []):
            if cookie.get("name") == key:
                return cookie.get("value")
        
        return None
    except Exception as e:
        print(f"Error reading {key} from storage: {e}")
        return None

async def browser_login():
    """Login via browser and save storage state."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Go to login page
        await page.goto("https://wichart.vn/login", wait_until="networkidle")

        # Fill form
        await page.fill('input[name="email"]', WICHART_EMAIL)
        await page.fill('input[name="password"]', WICHART_PASSWORD)

        # Submit
        await page.click('button[type="submit"]')

        # Wait until logged in
        await page.wait_for_url("**/**", timeout=60_000)

        # Wait for page to fully load before saving state
        await asyncio.sleep(10)

        # Save storage state
        await context.storage_state(path=STORAGE_PATH)
        print("Saved storage to wichart_storage.json")

        await browser.close()


@task(log_prints=True)
def get_token() -> (str, str, str):
    
    print("Lgging in via browser...")
    asyncio.run(browser_login())

    wtoken = get_from_storage(key="wtoken")
    device_token = get_from_storage(key="deviceToken")
    wid = get_from_storage(key="wid")

    if wtoken and device_token:
        print("Successfully obtained device_token and wid after browser login")
        return wtoken, device_token, wid,
    
    print("Failed to obtain device_token and wid")
    return None


@task
def crawl_wichart_report(token: str, device_token: str, wid: str):

    url = "https://wichart.vn/wichartapi/wichart/company/report?"
    nonce = getNonce()
    now = int(time.time() * 1000)
    keyword_search = ["bao_cao_vi_mo", "bao_cao_nganh", "bao_cao_doanh_nghiep", "bao_cao_chien_luoc"]

    result_df = pd.DataFrame()
    for search in keyword_search:
        for i in range(5):
            payload = {"desc": "true", "page": i + 1, "page_size": 10, "loaibaocao": search}
            new_url = url + urllib.parse.urlencode(payload)
            nonce = getNonce()
            now = int(time.time() * 1000)

            sign = {
                "desc": "true", "loaibaocao": payload['loaibaocao'], "nonce": nonce,
                "page": payload['page'], "page_size": payload['page_size'],
                "sign-token": "ObBeWhVmYs3tP2Nz$C$FJ@P4AQfTjlPX", "stime": now, "v": "v1"
            }
            
            headers = {
                'authority': 'wichart.vn', 'accept': 'application/json, text/plain, */*',
                'authorization': 'Bearer ' + token, 'content-type': 'application/json',
                'cookie': 'deviceToken=' + device_token + '; wid=' + wid + '; wtoken=' + token,
                'origin': 'https://wichart.vn', 'referer': 'https://wichart.vn/report',
                'sec-ch-ua': '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
                'sec-ch-ua-mobile': '?0', 'sec-ch-ua-platform': '"macOS"',
                'sec-fetch-dest': 'empty', 'sec-fetch-mode': 'cors', 'sec-fetch-site': 'same-origin',
                'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
                'v': 'v1', 'nonce': nonce, 'sign': getSign(sign), 'sign-token': "ObBeWhVmYs3tP2Nz$C$FJ@P4AQfTjlPX", 'stime': str(now),
            }

            try:
                response = requests.get(new_url, headers=headers)
                if response.status_code != 200:
                    print("Cannot call API: ", new_url)
                    print(response.text)
                    continue
                enc = response.json()['enc']
                key = "ZmRvaWFmaGRpc2ZoaWRzZHNoa2RoaW9zZGZoc2E=".encode()

                decrypted_text = decrypt.decrypt_aes(enc, key)
                data = json.loads(decrypted_text)
                if 'result' in data:
                    report = data['result']
                    result_df = pd.concat([result_df, pd.DataFrame(report)])
                
            except Exception as e:
                print(e)
                print("Cannot call API: ", new_url, " with response: ", response.text)
            
    return result_df

def save_to_delta_table(df: pd.DataFrame):
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
            df[col] = pd.to_datetime(df[col])

    # Convert DataFrame to PyArrow Table using the schema
    pyarrow_table = pa.Table.from_pandas(df, schema=wichart_report_schema)
    dt = DeltaTable(tablePath, storage_options=storage_options)
    if dt.is_deltatable():
        result = dt.merge(df, 
                          predicate="target.id = source.id", 
                          source_alias="source", 
                          target_alias="target").\
            when_not_matched_insert_all().execute()
    else:
        result = write_deltalake(tablePath, pyarrow_table, storage_options=storage_options, mode="overwrite")

    print(result)
    return

@flow(log_prints=True)
def crawl_wichart_report_workflow():
    """Flow: ETL for syncing tickers"""

    token, device_token, wid = get_token()
    if token is None or device_token is None or wid is None:
        print("Failed to obtain token, device_token and wid")
        return
    
    # Task 1: Crawling Wichart report
    df = crawl_wichart_report(token, device_token, wid)
    if df.empty:
        print("data is empty")
        return

    # Task 2: Save to Delta Table
    save_to_delta_table(df)
    

# Run the flow
if __name__ == "__main__":
    # crawl_wichart_workflow()

    crawl_wichart_report_workflow.from_source(
        source=str(Path(__file__).parent),  # code stored in local directory
        entrypoint="crawl_wichart_report_workflow.py:crawl_wichart_report_workflow",
    ).deploy(
        name="crawl_wichart_report_workflow",
        work_pool_name="my-worker",
        # Run at 9:00 AM and 3:00 PM from Monday to Friday
        cron="0 2,8 * * 1-5", ## UTC+0
    )
