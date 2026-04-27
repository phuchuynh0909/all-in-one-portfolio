#!/usr/bin/env python3
"""Standalone CLI script for running duplicate audit and merge-health checks.

Usage:
    python worker/run_audit.py --date 2026-03-26 --output evidence.json
"""

import argparse
import json
import logging
import sys
from datetime import date

from infra.audit_queries import run_duplicate_audit, run_merge_health
from infra.clickhouse_client import get_clickhouse_client
from config import config


def setup_logging() -> None:
    """Configure logging with standard format."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def main() -> int:
    """Run duplicate audit and merge health checks.

    Returns:
        0 if duplicate_key_groups == 0 (PASS), 1 if > 0 (FAIL)
    """
    parser = argparse.ArgumentParser(
        description="Run duplicate audit and merge-health checks on ClickHouse ticks table"
    )
    parser.add_argument(
        "--date",
        type=str,
        default=date.today().isoformat(),
        help="Date to audit (YYYY-MM-DD, default: today)",
    )
    parser.add_argument(
        "--table",
        type=str,
        default="ticks",
        help="Table name to audit (default: ticks)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional path to save JSON evidence file",
    )

    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger(__name__)

    try:
        # Get ClickHouse client
        ch_client = get_clickhouse_client()
        db = config.clickhouse.database
        table = args.table
        date_str = args.date

        logger.info(
            "Starting audit for %s.%s on %s",
            db,
            table,
            date_str,
        )

        # Run duplicate audit
        duplicate_audit = run_duplicate_audit(ch_client.client, db, table, date_str)

        # Run merge health check
        merge_health = run_merge_health(ch_client.client, db, table)

        # Determine pass/fail status
        duplicate_key_groups = duplicate_audit["duplicate_key_groups"]
        status = "PASS" if duplicate_key_groups == 0 else "FAIL"

        # Print formatted summary
        print(
            f"\n{'=' * 60}\n"
            f"  Audit Report  —  {date_str}\n"
            f"{'=' * 60}\n"
            f"  Table                    : {db}.{table}\n"
            f"  Status                   : {status}\n"
            f"\n  Duplicate Audit:\n"
            f"    Duplicate key groups   : {duplicate_key_groups:>10}\n"
            f"    Max version count      : {duplicate_audit['max_version_count']:>10}\n"
            f"    Sample keys (first 5)  : {len(duplicate_audit['sample_keys']):>10}\n"
            f"\n  Merge Health:\n"
            f"    Hot partitions (>10)   : {len(merge_health['hot_partitions']):>10}\n"
            f"    Total active parts     : {merge_health['total_active_parts']:>10}\n"
            f"{'=' * 60}\n"
        )

        # Save evidence if requested
        if args.output:
            evidence = {
                "date": date_str,
                "table": f"{db}.{table}",
                "duplicate_audit": duplicate_audit,
                "merge_health": merge_health,
                "status": status,
            }
            with open(args.output, "w") as f:
                json.dump(evidence, f, indent=2, default=str)
            logger.info("Evidence saved to %s", args.output)

        # Exit code based on duplicate status
        exit_code = 0 if duplicate_key_groups == 0 else 1
        logger.info("Audit complete — status=%s, exit_code=%d", status, exit_code)

        return exit_code

    except Exception as e:
        logger.exception("Audit failed with error: %s", e)
        print(f"\n{'=' * 60}")
        print(f"  ERROR: {e}")
        print(f"{'=' * 60}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
