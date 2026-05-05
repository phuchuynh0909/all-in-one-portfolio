#!/usr/bin/env python3
"""
Backfill VN30F tick data from DNSE API into ClickHouse.

Usage:
    python backfill_ticks.py
    python backfill_ticks.py --start 2025-01-01 --end 2026-03-26
    python backfill_ticks.py --dry-run
    python backfill_ticks.py --show-calendar

Contract symbols are resolved automatically per date via vn30f_symbol.
After backfill, run merge to build VN30F1M:
    python ohlc_5m.py --date-from "2025-01-01 00:00:00" --date-to "2026-03-27 00:00:00"
    then: python -c "from ohlc_5m import vn30f1m_pipeline; vn30f1m_pipeline('2025-01-01')"
"""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

# Repo root (`worker/`); needed when running `python scripts/backfill_ticks.py`.
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from infra.audit_queries import print_metrics
from workers.reconciler import run_reconciler
from core.vn30f_symbol import encode, front_month, third_thursday


def show_calendar(start: date, end: date) -> None:
    print(f"\nVN30F contract calendar ({start} → {end}):")
    print(f"{'Period':35s} Symbol")
    print("-" * 55)
    current = start
    prev_sym, period_start = None, start
    while current <= end:
        sym = encode(*front_month(current))
        if sym != prev_sym:
            if prev_sym:
                print(
                    f"{str(period_start):15s} → {str(current - timedelta(days=1)):15s}  {prev_sym}"
                )
            period_start, prev_sym = current, sym
        current += timedelta(days=1)
    if prev_sym:
        print(f"{str(period_start):15s} → {str(end):15s}  {prev_sym}")
    print()


def run(start: date, end: date, dry_run: bool = False) -> None:
    total_days = (end - start).days + 1
    trading_days = sum(
        1 for i in range(total_days) if (start + timedelta(days=i)).weekday() < 5
    )

    print(f"\nBackfill: {start} → {end}  ({trading_days} trading days)")
    if dry_run:
        print("DRY-RUN: will fetch from API but not write to ClickHouse\n")

    current = start
    done = 0
    total_fetched = total_patched = total_failed = 0

    while current <= end:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        done += 1
        pct = done / trading_days * 100
        m = run_reconciler(current.isoformat(), dry_run=dry_run)

        total_fetched += m.fetched_rows
        total_patched += m.patched_rows if not dry_run else 0
        total_failed += m.failed_rows

        status = "✓" if m.failed_rows == 0 else "✗"
        if m.fetched_rows > 0 or m.failed_rows > 0:
            print(
                f"[{done:3d}/{trading_days} {pct:5.1f}%] {status} {current}  "
                f"fetched={m.fetched_rows:5d}  patched={m.patched_rows:5d}  "
                f"failed={m.failed_rows}  {m.duration_s:.1f}s"
            )
        else:
            print(f"[{done:3d}/{trading_days} {pct:5.1f}%]   {current}  (no data)")

        current += timedelta(days=1)

    print(f"\n{'=' * 60}")
    print(f"Backfill complete")
    print(f"  Total fetched : {total_fetched:,}")
    print(f"  Total patched : {total_patched:,}")
    print(f"  Total failed  : {total_failed:,}")
    print(f"{'=' * 60}")

    if not dry_run and total_patched > 0:
        print("\nNext step — aggregate ticks into 1h OHLC + build VN30F1M:")
        print(
            f'  python ohlc_5m.py --date-from "{start} 00:00:00" --date-to "{end} 23:59:59"'
        )
        print(
            f"  python -c \"from ohlc_5m import vn30f1m_pipeline; vn30f1m_pipeline('{start}', '{end}')\""
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill VN30F tick data into ClickHouse"
    )
    parser.add_argument("--start", default="2025-01-01", metavar="YYYY-MM-DD")
    parser.add_argument("--end", default=date.today().isoformat(), metavar="YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--show-calendar", action="store_true", help="Print contract calendar and exit"
    )
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    if args.show_calendar:
        show_calendar(start, end)
        sys.exit(0)

    run(start, end, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
