#!/usr/bin/env python3
"""
Operational entrypoint for the tick reconciliation pipeline.

T12 VERIFIED: Failure modes in reconciler.py are correctly handled:
  - API 5xx: RequestException caught with retry-once (lines 188-193)
  - ClickHouse insert: Exception caught in patch_ticks, returns (0, failed_rows) (lines 163-180)
  - Outer exception: Caught at run_reconciler level, logs and sets failed_rows (lines 223-225)

Usage:
    python worker/run_pipeline.py                    # Normal daily run (at 15:00+)
    python worker/run_pipeline.py --dry-run --force  # Dry-run anytime
    python worker/run_pipeline.py --force            # Force rerun after 15:00
    python worker/run_pipeline.py --date 2026-03-25 # Run for specific date
"""

import argparse
import logging
import sys
from datetime import date

from audit_queries import run_duplicate_audit, run_merge_health
from clickhouse_client import get_clickhouse_client
from config import config
from reconciler import run_reconciler
from reconciler_schedule import get_last_run_date, should_run_today

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)


def print_section(title: str) -> None:
    """Print a formatted section header."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def show_pipeline_status(date_str: str, force: bool) -> bool:
    """Show pipeline status and check if reconciler should run.

    Returns:
        bool: True if reconciler should run, False otherwise.
    """
    print_section("Step 1: Pipeline Status")

    last_run = get_last_run_date()
    print(f"  Last run date        : {last_run or 'Never'}")
    print(f"  Target date          : {date_str}")
    print(f"  Force mode           : {force}")

    should_run = should_run_today(force=force)
    status = "✓ Ready to run" if should_run else "✗ Skipped (already ran today)"
    print(f"  Status               : {status}")

    return should_run


def run_reconciler_step(date_str: str, dry_run: bool) -> bool:
    """Run the reconciler and display metrics.

    Returns:
        bool: True if reconciler succeeded (failed_rows == 0), False otherwise.
    """
    print_section("Step 2: Reconciler")

    metrics = run_reconciler(date_str, dry_run=dry_run)

    # Metrics are already printed by print_metrics() in reconciler.py
    # Just return success status
    success = metrics.failed_rows == 0
    return success


def run_audit_step(date_str: str) -> bool:
    """Run duplicate audit and merge health checks.

    Returns:
        bool: True if audit passed (no duplicates), False otherwise.
    """
    print_section("Step 3: Audit")

    try:
        ch_client = get_clickhouse_client()
        db = config.clickhouse.database
        table = "ticks"

        log.info("Running duplicate audit for %s.%s on %s", db, table, date_str)
        duplicate_audit = run_duplicate_audit(ch_client.client, db, table, date_str)

        log.info("Running merge health check for %s.%s", db, table)
        merge_health = run_merge_health(ch_client.client, db, table)

        # Determine pass/fail
        duplicate_key_groups = duplicate_audit["duplicate_key_groups"]
        status = "PASS" if duplicate_key_groups == 0 else "FAIL"

        # Print formatted summary
        print(
            f"\n  Audit Report  —  {date_str}\n"
            f"  Status                   : {status}\n"
            f"\n  Duplicate Audit:\n"
            f"    Duplicate key groups   : {duplicate_key_groups:>10}\n"
            f"    Max version count      : {duplicate_audit['max_version_count']:>10}\n"
            f"    Sample keys (first 5)  : {len(duplicate_audit['sample_keys']):>10}\n"
            f"\n  Merge Health:\n"
            f"    Hot partitions (>10)   : {len(merge_health['hot_partitions']):>10}\n"
            f"    Total active parts     : {merge_health['total_active_parts']:>10}\n"
        )

        return duplicate_key_groups == 0

    except Exception as e:
        log.exception("Audit failed: %s", e)
        print(f"\n  ERROR: {e}\n")
        return False


def main() -> int:
    """Main entrypoint for the pipeline.

    Returns:
        0 if pipeline succeeded, 1 if any step failed.
    """
    parser = argparse.ArgumentParser(
        description="Operational entrypoint for tick reconciliation pipeline"
    )
    parser.add_argument(
        "--date",
        type=str,
        default=date.today().isoformat(),
        help="Date to reconcile (YYYY-MM-DD, default: today)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview reconciler changes without applying",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass schedule guard (run anytime)",
    )
    parser.add_argument(
        "--skip-audit",
        action="store_true",
        help="Skip audit step after reconciler",
    )

    args = parser.parse_args()

    # Step 1: Show status
    should_run = show_pipeline_status(args.date, args.force)
    if not should_run:
        print("\n  Pipeline skipped (schedule guard active)\n")
        return 0

    # Step 2: Run reconciler
    reconciler_ok = run_reconciler_step(args.date, args.dry_run)
    if not reconciler_ok:
        log.error("Reconciler failed — aborting pipeline")
        print("\n  Pipeline failed at reconciler step\n")
        return 1

    # Step 3: Run audit (optional)
    if args.skip_audit:
        print_section("Step 3: Audit")
        print("  Skipped (--skip-audit)\n")
        return 0

    audit_ok = run_audit_step(args.date)
    if not audit_ok:
        log.error("Audit failed — pipeline incomplete")
        print("\n  Pipeline completed with audit failures\n")
        return 1

    print("\n  Pipeline completed successfully\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
