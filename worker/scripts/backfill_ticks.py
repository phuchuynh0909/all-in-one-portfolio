#!/usr/bin/env python3
"""
Backfill authoritative DNSE ticks into ClickHouse.

Usage:
    python scripts/backfill_ticks.py --symbol BCM --start 2026-09-11 --end 2026-09-11
    python scripts/backfill_ticks.py --symbol BCM --symbol FPT --dry-run
    python scripts/backfill_ticks.py --show-calendar

When no ``--symbol`` is supplied, the VN30F front-month contract is resolved
per date. Every symbol/date is reconciled by DNSE event identity.
"""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

# Repo root (`worker/`); needed when running `python scripts/backfill_ticks.py`.
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
    
from workers.reconciler import run_reconciler
from core.vn30f_symbol import encode, front_month, symbol_for_date


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


def run(
    start: date,
    end: date,
    dry_run: bool = False,
    symbols: tuple[str, ...] = (),
) -> int:
    total_days = (end - start).days + 1
    trading_days = sum(
        1 for i in range(total_days) if (start + timedelta(days=i)).weekday() < 5
    )
    total_runs = trading_days * max(1, len(symbols))

    scope = ",".join(symbols) if symbols else "VN30F front month"
    print(f"\nBackfill: {start} → {end}  ({trading_days} trading days; {scope})")
    if dry_run:
        print("DRY-RUN: will fetch from API but not write to ClickHouse\n")

    current = start
    done = 0
    total_fetched = total_patched = total_failed = 0

    while current <= end:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        day_symbols = symbols or (symbol_for_date(current),)
        for symbol in day_symbols:
            done += 1
            pct = done / total_runs * 100
            m = run_reconciler(
                current.isoformat(), dry_run=dry_run, symbol=symbol
            )

            total_fetched += m.fetched_rows
            total_patched += m.patched_rows if not dry_run else 0
            total_failed += m.failed_rows

            status = "✓" if m.failed_rows == 0 else "✗"
            print(
                f"[{done:3d}/{total_runs} {pct:5.1f}%] {status} "
                f"{current} {symbol:12s} fetched={m.fetched_rows:5d}  "
                f"patched={m.patched_rows:5d}  failed={m.failed_rows}  "
                f"{m.duration_s:.1f}s"
            )

        current += timedelta(days=1)

    print(f"\n{'=' * 60}")
    print(f"Backfill complete")
    print(f"  Total fetched : {total_fetched:,}")
    print(f"  Total patched : {total_patched:,}")
    print(f"  Total failed  : {total_failed:,}")
    print(f"{'=' * 60}")

    return total_failed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill authoritative DNSE ticks into ClickHouse"
    )
    parser.add_argument("--start", default="2025-01-01", metavar="YYYY-MM-DD")
    parser.add_argument("--end", default=date.today().isoformat(), metavar="YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--show-calendar", action="store_true", help="Print contract calendar and exit"
    )
    parser.add_argument(
        "--symbol",
        action="append",
        default=[],
        help="Symbol to reconcile; repeat for multiple symbols (default: VN30F front month)",
    )
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    if args.show_calendar:
        show_calendar(start, end)
        sys.exit(0)

    failed = run(
        start,
        end,
        dry_run=args.dry_run,
        symbols=tuple(dict.fromkeys(symbol.strip().upper() for symbol in args.symbol)),
    )
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
