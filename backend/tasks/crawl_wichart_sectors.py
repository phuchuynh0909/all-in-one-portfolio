from typing import List, Dict, Any
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import requests
from prefect import task, flow
import pyarrow as pa
from deltalake.writer import try_get_deltatable
from deltalake import write_deltalake
import time

# Allow importing from project root (to access `constants`)
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from constants import SECTOR_WICHART  # noqa: E402
from task_management.wichart_report import getNonce, getSign, getToken  # noqa: E402
from database import DatabaseConnection
from task_management import decrypt  # noqa: E402 (local module)
from dotenv import load_dotenv
load_dotenv()

def _build_headers_and_url(
    *,
    token: str,
    list_id: int,
    key: str,
    type_value: int,
    from_date: str,
    to_date: str,
) -> Dict[str, Any]:
    """Build signed request components for the Wichart sector price API."""
    url = (
        "https://wichart.vn/wichartapi/sector/nganh/gia"
        f"?key={key}&type={type_value}&listID[]={list_id}&from={from_date}&to={to_date}"
    )

    nonce = getNonce()
    now_ms = int(time.time() * 1000)


    sign_payload = {
        "from": from_date,
        "key": key,
        "listID": list_id,
        "nonce": nonce,
        "sign-token": "ObBeWhVmYs3tP2Nz$C$FJ@P4AQfTjlPX",
        "stime": now_ms,
        "to": to_date,
        "type": type_value,
        "v": "v1",
    }
    signature = getSign(sign_payload)

    headers = {
        "authority": "wichart.vn",
        "accept": "application/json, text/plain, */*",
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
        # Important: cookie must include the bearer token via wtoken
        "cookie": (
            "deviceToken=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJ1bmlxdWVJRCI6ImFlYWRiNGQ0NTI5MDRjYWFmYTkzMjZhYjQ1OTUyYzY4IiwiZXhw"
            "aXJlcyI6IjIwMjUtMTItMTVUMDU6MDk6MTAuMTIzWiIsImlhdCI6MTczNDIzOTM1MH0."
            "mp6nwgEg1jIvsLk2rj4y8KwomS8H9oEk5AONNvmc2Pc; wid=zZZ87Fb9f21VeYwiLfMq; "
            f"wtoken={token}"
        ),
        "origin": "https://wichart.vn",
        "referer": "https://wichart.vn/report",
        "sec-ch-ua": '"Not/A)Brand";v="99", "Google Chrome";v="115", "Chromium";v="115"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        ),
        # Upper-cased headers used by this endpoint
        "V": "v1",
        "Nonce": nonce,
        "Sign": signature,
        "Sign-Token": "ObBeWhVmYs3tP2Nz$C$FJ@P4AQfTjlPX",
        "Stime": str(now_ms)
    }

    return {"url": url, "headers": headers}


def _decode_response_to_rows(key: str, response_json: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Decode Wichart encrypted response and flatten into row dicts."""
    enc = response_json["enc"]
    key_bytes = "ZmRvaWFmaGRpc2ZoaWRzZHNoa2RoaW9zZGZoc2E=".encode()
    decrypted_text = decrypt.decrypt_aes(enc, key_bytes)
    import json as _json
    parsed = _json.loads(decrypted_text)

    rows: List[Dict[str, Any]] = []
    if isinstance(parsed, list):
        for item in parsed:
            sector_id = item.get("id")
            sector_name = item.get("name")
            series = item.get("data", []) or []
            for ts_ms, value in series:
                dt = datetime.fromtimestamp(ts_ms / 1000.0)
                rows.append(
                    {
                        "date": dt,
                        key: float(value) if value is not None else None,
                        "sector_id": int(sector_id) if sector_id is not None else None,
                        "sector_name": sector_name,
                    }
                )
    return rows

@task
def crawl_wichart_sectors_level_3(
    *,
    key: str = "close",
    sector_type: int = 3,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> pd.DataFrame:
    """Crawl all sectors defined in SECTOR_WICHART.

    Returns a DataFrame with columns: date, value, sector_id, sector_name.
    """
    if to_date is None:
        to_date = datetime.now().strftime("%Y-%m-%d")
    if from_date is None:
        # default 1 year back
        from_date = (datetime.now() - timedelta(days=365 * 1)).strftime("%Y-%m-%d")

    token = getToken()
    all_rows: List[Dict[str, Any]] = []

    for sector in SECTOR_WICHART:
        list_id = int(sector["id"])  # ensure int
        try:
            req = _build_headers_and_url(
                token=token,
                list_id=list_id,
                key=key,
                type_value=sector_type,
                from_date=from_date,
                to_date=to_date,
            )
            print(req["url"])
            response = requests.get(req["url"], headers=req["headers"], timeout=30)
            if response.status_code != 200:
                print(f"Cannot call API for sector {list_id}: {response.text}")
                continue

            rows = _decode_response_to_rows(key, response.json())
            all_rows.extend(rows)
        except Exception as exc:
            print(f"Error crawling sector {list_id}: {exc}")

    df = pd.DataFrame(all_rows)
    if not df.empty:
        df['sector_type'] = sector_type
        df["key"] = df["sector_id"].astype(str) + "_" + df["date"].dt.strftime("%Y-%m-%d")
    # print(df)
    return df

@task
def crawl_wichart_sectors_level_4(
    *,
    key: str = "close",
    sector_type: int = 4,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> pd.DataFrame:
    """Crawl all sectors defined in SECTOR_WICHART.

    Returns a DataFrame with columns: date, value, sector_id, sector_name.
    """
    if to_date is None:
        to_date = datetime.now().strftime("%Y-%m-%d")
    if from_date is None:
        # default 1 year back
        from_date = (datetime.now() - timedelta(days=365 * 1)).strftime("%Y-%m-%d")
    
    token = getToken()
    all_rows: List[Dict[str, Any]] = []

    ## Get sector level_4 from database
    db = DatabaseConnection()
    db.initialize_database()
    sector_level_4 = db.query_sector_information(level=4, filters=[("vonhoa_d", ">", 10000)])
    for index, row in sector_level_4.iterrows():
        sector_id = row["id"]
        try:
            req = _build_headers_and_url(
                token=token,
                list_id=sector_id,
                key=key,
                type_value=sector_type,
                from_date=from_date,
                to_date=to_date,
            )
            response = requests.get(req["url"], headers=req["headers"], timeout=30)
            if response.status_code != 200:
                print(f"Cannot call API for sector {sector_id}: {response.text}")
                continue
            rows = _decode_response_to_rows(key, response.json())
            all_rows.extend(rows)
        except Exception as exc:    
            print(f"Error crawling sector {sector_id}: {exc}")
            continue
    df = pd.DataFrame(all_rows)
    if not df.empty:
        df['sector_type'] = sector_type
        df["key"] = df["sector_id"].astype(str) + "_" + df["date"].dt.strftime("%Y-%m-%d")
    return df

def save_sectors_to_delta_table(df: pd.DataFrame, table_path: str) -> None:
    """Save sector price time series to a Delta table, upserting by key."""
    storage_options = {
        "AWS_ACCESS_KEY_ID": os.getenv("MINIO_ACCESS_KEY"),
        "AWS_SECRET_ACCESS_KEY": os.getenv("MINIO_SECRET_KEY"),
        "AWS_ENDPOINT_URL": f"http://{os.getenv('MINIO_ENDPOINT')}",
        "AWS_ALLOW_HTTP": "true",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_REGION": 'ap-southeast-1',
        "aws_conditional_put": "etag",
    }

    write_deltalake(table_path, df, storage_options=storage_options, mode="overwrite")
    # pyarrow_table = pa.Table.from_pandas(df)
    # dt = try_get_deltatable(table_path, storage_options=storage_options)
    # if dt is not None:
    #     write_deltalake(table_path, pyarrow_table, storage_options=storage_options, mode="overwrite")
    #     # Upsert on key
    #     # dt.merge(pyarrow_table, 
    #     #          predicate="target.key = source.key", 
    #     #          source_alias="source", target_alias="target")\
    #     #     .when_not_matched_insert_all()\
    #     #     .when_matched_update(predicate="target.key = source.key " \
    #             # "AND target.close != source.close", updates={"close": "source.close"}).execute()
    # else:
    #     write_deltalake(table_path, pyarrow_table, storage_options=storage_options, mode="overwrite")


@flow(log_prints=True)
def sync_wichart_sectors(table_path: str = "s3://delta-table-storage/wichart_sector") -> None:
    df = crawl_wichart_sectors_level_3()
    if df.empty:
        print("No sector data crawled.")
        return
    
    df_level_4 = crawl_wichart_sectors_level_4()
    if df_level_4.empty:
        print("No sector data crawled.")
        return
    
    df = pd.concat([df, df_level_4])
    save_sectors_to_delta_table(df, table_path)


if __name__ == "__main__":
    # sync_wichart_sectors()
    sync_wichart_sectors.from_source(
        source=str(Path(__file__).parent),
        entrypoint="wichart_sector.py:sync_wichart_sectors",
    ).deploy(
        name="sync-wichart-sectors",
        work_pool_name="my-worker",
        # Run daily at 7:30 AM UTC (adjust as needed)
        cron="0 8 * * 1-5",
    )


