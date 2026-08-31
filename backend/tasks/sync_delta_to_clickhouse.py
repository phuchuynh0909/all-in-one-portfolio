"""Sync Delta table → ClickHouse by date range.

Reads directly from the Delta table snapshot (no CDF required).
Useful for backfilling, re-syncing a specific day, or recovering from CDF gaps.

Usage:
    # single day
    python sync_delta_to_clickhouse.py --date 2026-04-15

    # date range
    python sync_delta_to_clickhouse.py --from-date 2026-01-01 --to-date 2026-04-15

    # last N days
    python sync_delta_to_clickhouse.py --last-days 20

    # everything (full backfill)
    python sync_delta_to_clickhouse.py --all
"""

import argparse
import gc
import json
import os
from datetime import date, timedelta
from typing import Any

import pandas as pd
from clickhouse_driver import Client  # type: ignore
from deltalake import DeltaTable


SECTOR_WICHART = [
  { "id": 70, "name": "Ngân hàng" },
  { "id": 1, "name": "Quản lý và phát triển bất động sản" },
  { "id": 26, "name": "Tập đoàn công nghiệp" },
  { "id": 58, "name": "Sản phẩm thực phẩm" },
  { "id": 72, "name": "Thị trường vốn" },
  { "id": 35, "name": "Cơ sở hạ tầng giao thông" },
  { "id": 67, "name": "Kim loại và khai khoáng" },
  { "id": 41, "name": "Dịch vụ viễn thông đa dạng" },
  { "id": 17, "name": "Phần mềm" },
  { "id": 51, "name": "Khách sạn, nhà hàng và giải trí" },
  { "id": 36, "name": "Tiện ích điện" },
  { "id": 32, "name": "Vận tải hành khách hàng không" },
  { "id": 68, "name": "Giấy và lâm sản" },
  { "id": 37, "name": "Tiện ích khí đốt" },
  { "id": 55, "name": "Bán lẻ chuyên dụng" },
  { "id": 64, "name": "Hóa chất" },
  { "id": 24, "name": "Kỹ thuật xây dựng" },
  { "id": 54, "name": "Bán lẻ đa kênh" },
  { "id": 33, "name": "Vận tải đường biển" },
  { "id": 57, "name": "Đồ uống" },
  { "id": 74, "name": "Bảo hiểm" },
  { "id": 63, "name": "Dầu, khí đốt và nhiên liệu tiêu hao" },
  { "id": 65, "name": "Vật liệu xây dựng" },
  { "id": 50, "name": "Hàng may mặc, phụ kiện và hàng hóa xa xỉ" },
  { "id": 14, "name": "Dược phẩm" },
  { "id": 39, "name": "Tiện ích nước" },
  { "id": 48, "name": "Hàng tiêu dùng lâu bền" },
  { "id": 62, "name": "Thiết bị và dịch vụ năng lượng" },
  { "id": 69, "name": "Dịch vụ tài chính" },
  { "id": 34, "name": "Vận tải đường bộ và đường sắt" },
  { "id": 53, "name": "Nhà phân phối" },
  { "id": 25, "name": "Thiết bị điện" },
  { "id": 16, "name": "Dịch vụ công nghệ thông tin" },
  { "id": 29, "name": "Dịch vụ và cung cấp vật tư thương mại" },
  { "id": 43, "name": "Phương tiện truyền thông" },
  { "id": 46, "name": "Linh kiện ô tô" },
  { "id": 11, "name": "Nhà cung cấp và dịch vụ chăm sóc sức khỏe" },
  { "id": 66, "name": "Thùng đựng và bao bì" },
  { "id": 40, "name": "Các nhà sản xuất điện độc lập và điện tái tạo" },
  { "id": 60, "name": "Đồ gia dụng" },
  { "id": 31, "name": "Vận tải hàng không và logistics" },
  { "id": 27, "name": "Máy móc" },
  { "id": 59, "name": "Thuốc lá" },
  { "id": 20, "name": "Thiết bị, dụng cụ và linh kiện điện tử" },
  { "id": 28, "name": "Công ty thương mại và nhà phân phối" },
  { "id": 47, "name": "Ô tô" },
  { "id": 10, "name": "Thiết bị và vật tư chăm sóc sức khỏe" },
  { "id": 49, "name": "Sản phẩm giải trí" },
  { "id": 52, "name": "Dịch vụ tiêu dùng đa dạng" },
  { "id": 44, "name": "Giải trí" },
  { "id": 30, "name": "Dịch vụ chuyên nghiệp" }
]

# ── config ────────────────────────────────────────────────────────────────────

DELTA_SRC  = os.getenv("DELTA_TABLE_PATH", "s3://delta-table-storage/stocks")
BATCH_SIZE = int(os.getenv("CH_BATCH_SIZE", "50000"))


def _get_env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _get_delta_storage_options() -> dict[str, str]:
    return {
        "AWS_ACCESS_KEY_ID":         _get_env("AWS_ACCESS_KEY_ID",        "CzOwnLkEDXQy951AOqes"),
        "AWS_SECRET_ACCESS_KEY":     _get_env("AWS_SECRET_ACCESS_KEY",    "fdRe91TOtqTl0icUkZLsUnWvZa90aZ5qG5rVEf7S"),
        "AWS_ENDPOINT_URL":          _get_env("AWS_ENDPOINT_URL",         "http://192.168.1.3:9000"),
        "AWS_ALLOW_HTTP":            "true",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_REGION":                _get_env("AWS_REGION",               "ap-southeast-1"),
        "aws_conditional_put":       "etag",
    }


def _get_ch_client() -> Client:
    host = _get_env("CLICKHOUSE_HOST", "192.168.1.3")
    port = int(_get_env("CLICKHOUSE_PORT", "9010"))
    try:
        return Client(
            host     = host,
            port     = port,
            user     = _get_env("CLICKHOUSE_USER",     "kyostyle1"),
            password = _get_env("CLICKHOUSE_PASSWORD", "kyostyle1"),
            database = _get_env("CLICKHOUSE_DB",       "default"),
        )
    except Exception as e:
        raise RuntimeError(
            f"ClickHouse connection failed at {host}:{port}. "
            f"Use CLICKHOUSE_HOST / CLICKHOUSE_PORT env vars to override. "
            f"Port must be the native TCP port (not HTTP 8123). Error: {e}"
        ) from e


def _ensure_table(client: Client, database: str, table: str) -> None:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
    client.execute(f"""
        CREATE TABLE IF NOT EXISTS {database}.{table} (
            date    Date,
            symbol  String,
            open    Float64,
            high    Float64,
            low     Float64,
            close   Float64,
            volume  Float64,
            ver     DateTime64(3) DEFAULT now64(3)
        )
        ENGINE = ReplacingMergeTree(ver)
        PARTITION BY intDiv(toYear(date) - 1970, 5)
        ORDER BY (symbol, date)
    """)


# ── core ──────────────────────────────────────────────────────────────────────

def read_delta_by_date(
    from_date: date,
    to_date: date,
    src: str = DELTA_SRC,
) -> pd.DataFrame:
    """Read OHLCV rows from the Delta table for the given date range."""
    storage_options = _get_delta_storage_options()
    dt = DeltaTable(src, storage_options=storage_options)

    print(f"Reading Delta: {src}")
    print(f"  Date range : {from_date} → {to_date}")

    df = dt.to_pandas(
        filters=[
            ("date", ">=", pd.Timestamp(from_date)),
            ("date", "<=", pd.Timestamp(to_date)),
        ],
        columns=["date", "symbol", "open", "high", "low", "close", "volume"],
    )

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["date", "symbol", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates(subset=["date", "symbol"], keep="last")

    print(f"  Rows read  : {len(df):,}")
    return df


def insert_to_clickhouse(df: pd.DataFrame) -> int:
    """Insert rows into ClickHouse. ReplacingMergeTree deduplicates on (symbol, date)."""
    database = _get_env("CLICKHOUSE_DB", "default")
    table    = _get_env("CLICKHOUSE_OHLC_EOD_TABLE", "ohlc_eod")
    client   = _get_ch_client()
    _ensure_table(client, database, table)

    inserted = 0
    for start in range(0, len(df), BATCH_SIZE):
        chunk = df.iloc[start : start + BATCH_SIZE]
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
            f"INSERT INTO {database}.{table}"
            f" (date, symbol, open, high, low, close, volume) VALUES",
            rows,
            types_check=False,
        )
        inserted += len(rows)
        print(f"  Inserted batch: {start:,} – {start + len(rows):,}")

    return inserted


def sync(from_date: date, to_date: date, src: str = DELTA_SRC) -> int:
    df = read_delta_by_date(from_date, to_date, src=src)
    if df.empty:
        print("No rows found for the given date range.")
        return 0

    inserted = insert_to_clickhouse(df)
    del df
    gc.collect()

    print(f"\nDone — {inserted:,} rows inserted into ClickHouse.")
    return inserted


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync Delta table → ClickHouse by date range"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--date",
        type=_parse_date,
        metavar="YYYY-MM-DD",
        help="Sync a single date",
    )
    group.add_argument(
        "--last-days",
        type=int,
        metavar="N",
        help="Sync the last N calendar days (today − N → today)",
    )
    group.add_argument(
        "--from-date",
        type=_parse_date,
        metavar="YYYY-MM-DD",
        help="Start of date range (requires --to-date)",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Sync everything in the Delta table (full backfill)",
    )

    parser.add_argument(
        "--to-date",
        type=_parse_date,
        metavar="YYYY-MM-DD",
        default=date.today(),
        help="End of date range (default: today)",
    )
    parser.add_argument(
        "--src",
        default=DELTA_SRC,
        help="Delta table S3 path",
    )

    args = parser.parse_args()

    if args.date:
        from_date = to_date = args.date
    elif args.last_days:
        to_date   = date.today()
        from_date = to_date - timedelta(days=args.last_days)
    elif args.from_date:
        from_date = args.from_date
        to_date   = args.to_date
    else:  # --all
        from_date = date(2000, 1, 1)
        to_date   = date.today()

    sync(from_date=from_date, to_date=to_date, src=args.src)


if __name__ == "__main__":
    main()
