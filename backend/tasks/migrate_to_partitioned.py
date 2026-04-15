"""One-time migration: rewrite existing Delta table with year partitioning.

Reads the existing unpartitioned table in batches of N rows to avoid OOM,
then writes each batch to a new year-partitioned table.

After migration, point crawl_ohlc_data.py / sync_ohlc.py at the new path.

Usage:
    python migrate_to_partitioned.py
    python migrate_to_partitioned.py --src s3://delta-table-storage/stocks \
                                     --dst s3://delta-table-storage/stocks-partitioned
    python migrate_to_partitioned.py --batch-size 100000
"""

import argparse
import gc
import os
import pandas as pd
import pyarrow as pa
from deltalake import DeltaTable
from deltalake.writer import write_deltalake


def _get_storage_options() -> dict[str, str]:
    return {
        "AWS_ACCESS_KEY_ID":        os.getenv("AWS_ACCESS_KEY_ID",        "CzOwnLkEDXQy951AOqes"),
        "AWS_SECRET_ACCESS_KEY":    os.getenv("AWS_SECRET_ACCESS_KEY",    "fdRe91TOtqTl0icUkZLsUnWvZa90aZ5qG5rVEf7S"),
        "AWS_ENDPOINT_URL":         os.getenv("AWS_ENDPOINT_URL",         "http://localhost:9000"),
        "AWS_ALLOW_HTTP":           "true",
        "AWS_EC2_METADATA_DISABLED":"true",
        "AWS_REGION":               os.getenv("AWS_REGION",               "ap-southeast-1"),
        "aws_conditional_put":      "etag",
    }


def migrate(src: str, dst: str, batch_size: int = 50_000) -> None:
    storage_options = _get_storage_options()

    print(f"Source : {src}")
    print(f"Dest   : {dst}")

    dt      = DeltaTable(src, storage_options=storage_options)
    dataset = dt.to_pyarrow_dataset()
    scanner = dataset.scanner(batch_size=batch_size)

    total_rows  = 0
    batch_num   = 0
    first_write = True

    for record_batch in scanner.to_batches():
        if len(record_batch) == 0:
            continue

        batch_num += 1
        df = record_batch.to_pandas()

        # Normalise date
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])

        # Add partition column
        df["year"] = df["date"].dt.year.astype(str)   # "2024"

        # Ensure key column exists
        if "key" not in df.columns:
            df["key"] = df["symbol"] + "_" + df["date"].dt.strftime("%Y-%m-%d")

        cols = [c for c in ["key", "symbol", "date", "year", "open", "high", "low", "close", "volume"]
                if c in df.columns]
        df = df[cols]

        arrow_table = pa.Table.from_pandas(df, preserve_index=False)
        del df, record_batch

        write_deltalake(
            dst,
            arrow_table,
            mode="overwrite" if first_write else "append",
            partition_by=["year"],
            storage_options=storage_options,
            engine="rust",
            schema_mode="merge",
            # Overwrite only on first batch so subsequent batches append into
            # the same partitioned table without wiping previous batches.
        )

        total_rows += len(arrow_table)
        print(f"  Batch {batch_num}: {len(arrow_table):,} rows written (total {total_rows:,})")
        del arrow_table
        gc.collect()
        first_write = False

    print(f"\nMigration complete. {total_rows:,} rows written to {dst}")
    print("Next steps:")
    print("  1. Verify row counts match the source table.")
    print("  2. Update destination in crawl_ohlc_data.py / sync_ohlc.py.")
    print("  3. Optionally delete or archive the old unpartitioned table.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate Delta table to year partitioning")
    parser.add_argument("--src", default="s3://delta-table-storage/stocks",
                        help="Source (unpartitioned) Delta table path")
    parser.add_argument("--dst", default="s3://delta-table-storage/stocks-partitioned",
                        help="Destination (year-partitioned) Delta table path")
    parser.add_argument("--batch-size", type=int, default=1_000_000,
                        help="Rows per read batch (lower = less RAM, more batches)")
    args = parser.parse_args()

    migrate(src=args.src, dst=args.dst, batch_size=args.batch_size)
