#!/usr/bin/env python3
"""
Crawl tick data from DNSE API and store in Parquet format.
Paginates backward using 'before' timestamp, from today to 2026-01-01.
"""

import time
import logging
from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import requests
import pandas as pd

# --- Config ---
SYMBOL = "41I1G4000"
BOARD = 2
LIMIT = 100000
START_DATE = date(2026, 1, 1)
END_DATE = date.today()
OUTPUT_DIR = Path("data")
REQUEST_DELAY = 0.1  # seconds between requests

API_URL = "https://api.dnse.com.vn/price-api/query"
HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9,vi;q=0.8",
    "cache-control": "max-age=0",
    "content-type": "application/json",
    "origin": "https://banggia.dnse.com.vn",
    "referer": "https://banggia.dnse.com.vn/",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    ),
}

# First page: omit `before`. Later pages: include `before` for pagination cursor.
GRAPHQL_QUERY = """
query GetKrxTicksBySymbols {{
    GetKrxTicksBySymbols(symbols: "{symbol}", date: "{date}", limit: {limit}{before_clause}, board: {board}) {{
      ticks {{
        symbol
        matchPrice
        matchQtty
        sendingTime
        side
      }}
    }}
  }}
"""

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def fetch_ticks(
    symbol: str, date_str: str, before: str | None, board: int, limit: int
) -> list[dict]:
    if before is None:
        before_clause = ""
    else:
        before_clause = f', before: "{before}"'
    payload = {
        "query": GRAPHQL_QUERY.format(
            symbol=symbol,
            date=date_str,
            limit=limit,
            before_clause=before_clause,
            board=board,
        )
    }
    resp = requests.post(API_URL, headers=HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        log.warning("GraphQL errors (date=%s, before=%s): %s", date_str, before, data["errors"])
    ticks = data.get("data", {}).get("GetKrxTicksBySymbols", {}).get("ticks", [])
    # API may return null payload when session errors / no permission
    if data.get("data") is None and not data.get("errors"):
        log.warning("GraphQL returned data=null (date=%s, before=%s)", date_str, before)
    return ticks or []


def crawl_day(symbol: str, day: date, board: int) -> pd.DataFrame:
    date_str = day.isoformat()
    if day > date.today():
        log.warning(
            "%s is after today (%s): there is usually no tick data for future calendar dates.",
            date_str,
            date.today().isoformat(),
        )

    # First request: no `before`. Some API builds return [] unless `before` anchors the window;
    # in that case we retry once with end-of-day UTC (same as older crawl behavior).
    before: str | None = None
    all_ticks: list[dict] = []
    page = 0

    while True:
        page += 1
        log.debug("  Page %d | before=%s", page, before if before is not None else "(none)")

        ticks = fetch_ticks(symbol, date_str, before, board, LIMIT)

        if not ticks and page == 1 and before is None:
            log.info(
                "  No ticks on first call without `before` for %s — retrying with before=end-of-day UTC",
                date_str,
            )
            before = f"{date_str}T23:59:59.999Z"
            ticks = fetch_ticks(symbol, date_str, before, board, LIMIT)

        if not ticks:
            break

        all_ticks.extend(ticks)
        log.debug("  Fetched %d ticks (total %d)", len(ticks), len(all_ticks))

        # If fewer results than limit, we've reached the beginning
        if len(ticks) < LIMIT:
            break

        # Advance cursor to oldest tick in this batch
        before = ticks[-1]["sendingTime"]
        time.sleep(REQUEST_DELAY)

    if not all_ticks:
        return pd.DataFrame()

    df = pd.DataFrame(all_ticks)
    df["sendingTime"] = pd.to_datetime(df["sendingTime"], format="ISO8601", utc=True)
    df["matchPrice"] = df["matchPrice"].astype(float)
    df["matchQtty"] = df["matchQtty"].astype("Int64")
    df["side"] = df["side"].astype("Int64")
    return df


PARQUET_SCHEMA = pa.schema([
    ("symbol", pa.string()),
    ("matchPrice", pa.float64()),
    ("matchQtty", pa.int64()),  # nullable
    ("sendingTime", pa.timestamp("us", tz="UTC")),
    ("side", pa.int64()),       # nullable
])


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{SYMBOL}_{START_DATE}_{END_DATE}.parquet"

    current = END_DATE
    total_records = 0
    writer: pq.ParquetWriter | None = None

    try:
        while current >= START_DATE:
            date_str = current.isoformat()
            log.info("Crawling %s ...", date_str)
            try:
                df = crawl_day(SYMBOL, current, BOARD)
            except requests.RequestException as e:
                log.error("Request failed for %s: %s — retrying once", date_str, e)
                time.sleep(5)
                try:
                    df = crawl_day(SYMBOL, current, BOARD)
                except requests.RequestException as e2:
                    log.error("Skipping %s after second failure: %s", date_str, e2)
                    current -= timedelta(days=1)
                    continue

            if df.empty:
                log.info("  No data for %s", date_str)
            else:
                table = pa.Table.from_pandas(df, schema=PARQUET_SCHEMA, preserve_index=False)
                if writer is None:
                    writer = pq.ParquetWriter(out_path, schema=PARQUET_SCHEMA, compression="snappy")
                writer.write_table(table)
                total_records += len(df)
                log.info("  %d records written (running total: %d)", len(df), total_records)

            current -= timedelta(days=1)
            time.sleep(REQUEST_DELAY)
    finally:
        if writer:
            writer.close()

    if total_records:
        log.info("Done. Saved %d records -> %s", total_records, out_path)
    else:
        log.info("Done. No data found.")


if __name__ == "__main__":
    main()
