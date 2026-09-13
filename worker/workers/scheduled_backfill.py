"""Run authoritative tick reconciliation once after each weekday session.

The process is intentionally a small, always-on scheduler so Docker Compose does
not depend on an external cron or Prefect server. At each due run it reconciles
every migrated historical symbol/session pair plus every symbol in the latest
completed session, then records success in the shared worker state volume.
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime
from typing import Callable
from zoneinfo import ZoneInfo

from config import config
from infra.audit_queries import ReconcilerMetrics
from infra.clickhouse_client import get_clickhouse_client
from infra.logging_setup import setup_logging
from infra.reconciler_schedule import (
    get_pending_repair_dates,
    mark_repair_done,
    mark_repair_pending,
    mark_run_done,
    should_run_today,
)
from model import (
    LARGE_ORDER_BLOCKS_TABLE,
    LEGACY_EVENT_SEQUENCE_START,
    OHLC_5M_AGG_TABLE,
    TICKS_CLICKHOUSE_TABLE,
    TRADE_FLOW_SECONDS_TABLE,
)
from workers import reconciler as tick_reconciler

setup_logging()
log = logging.getLogger(__name__)

MetricRunner = Callable[..., ReconcilerMetrics]
LegacyDeleter = Callable[[list[tuple[str, str]]], int]
WorkItem = tuple[date, str]
WorkDiscovery = Callable[[date], tuple[WorkItem, ...]]

run_reconciler = tick_reconciler.run_reconciler
delete_legacy_pairs = tick_reconciler.delete_legacy_pairs


def discover_reconciliation_work(cutoff_date: date) -> tuple[WorkItem, ...]:
    """Find historical legacy pairs and all symbols in the latest data date."""
    client = get_clickhouse_client()
    db = config.clickhouse.database
    cutoff = cutoff_date.isoformat()
    result = client.query(
        f"""
        WITH (
            SELECT max(toDate(sending_time))
            FROM {db}.{TICKS_CLICKHOUSE_TABLE}
            WHERE toDate(sending_time) <= '{cutoff}'
        ) AS latest_data_date
        SELECT session_date, symbol
        FROM
        (
            SELECT toDate(sending_time) AS session_date, symbol
            FROM {db}.{TICKS_CLICKHOUSE_TABLE}
            WHERE toDate(sending_time) <= '{cutoff}'
              AND event_sequence >= {LEGACY_EVENT_SEQUENCE_START}
            GROUP BY session_date, symbol

            UNION DISTINCT

            SELECT toDate(sending_time) AS session_date, symbol
            FROM {db}.{TICKS_CLICKHOUSE_TABLE}
            WHERE toDate(sending_time) = latest_data_date
            GROUP BY session_date, symbol
        )
        ORDER BY session_date DESC, symbol
        """
    )
    return tuple((row[0], str(row[1])) for row in result.result_rows)


def repair_derived_session(session_date: date) -> None:
    """Rebuild deployed derived tables after authoritative tick replacement."""
    client = get_clickhouse_client().client
    db = config.clickhouse.database

    if client.command(f"EXISTS {db}.{TRADE_FLOW_SECONDS_TABLE}"):
        from workers.block_episode_ingest import backfill

        backfill(client, db, session_date, session_date)
    if client.command(f"EXISTS {db}.{LARGE_ORDER_BLOCKS_TABLE}"):
        from workers.large_order_ingest import backfill

        backfill(client, db, session_date, session_date)
    if client.command(f"EXISTS {db}.{OHLC_5M_AGG_TABLE}"):
        from workers.ohlc_5m import rewrite_session

        rewrite_session(client, db, session_date)


def run_due_backfill(
    *,
    now: datetime | None = None,
    force: bool = False,
    runner: MetricRunner = run_reconciler,
    repair: Callable[[date], None] = repair_derived_session,
    discover: WorkDiscovery = discover_reconciliation_work,
    delete_legacy: LegacyDeleter = delete_legacy_pairs,
    mark_done: Callable[[str], None] = mark_run_done,
) -> list[ReconcilerMetrics] | None:
    """Reconcile every discovered symbol/session pair when the job is due.

    Legacy pairs disappear from discovery only after their authoritative DNSE
    replacement succeeds. Repair dates are persisted before tick mutation so a
    container restart cannot strand derived tables in a partially rebuilt state.
    """
    tz_name = config.tick_sync.session_tz
    tz = ZoneInfo(tz_name)
    local_now = datetime.now(tz) if now is None else now.astimezone(tz)
    if not should_run_today(
        tz_name=tz_name,
        trigger_hour=config.reconciler.reconciler_hour,
        trigger_minute=config.reconciler.reconciler_minute,
        force=force,
        now=local_now,
    ):
        return None

    run_date = local_now.date().isoformat()
    work_by_date: dict[date, list[str]] = {}
    for session_date, symbol in discover(local_now.date()):
        symbols = work_by_date.setdefault(session_date, [])
        if symbol not in symbols:
            symbols.append(symbol)

    pending_dates = {
        date.fromisoformat(value) for value in get_pending_repair_dates()
    }
    session_dates = sorted(set(work_by_date) | pending_dates, reverse=True)
    pair_count = sum(len(symbols) for symbols in work_by_date.values())
    log.info(
        "Starting after-session backfill: %d symbol/session pair(s) across %d date(s)",
        pair_count,
        len(session_dates),
    )

    metrics: list[ReconcilerMetrics] = []
    months = sorted({value.strftime("%Y-%m") for value in session_dates}, reverse=True)
    for month in months:
        month_dates = [
            value for value in session_dates if value.strftime("%Y-%m") == month
        ]
        for session_date in month_dates:
            mark_repair_pending(session_date.isoformat())

        successful_pairs: list[tuple[str, str]] = []
        for session_date in month_dates:
            date_str = session_date.isoformat()
            for symbol in work_by_date.get(session_date, []):
                result = runner(
                    date_str,
                    False,
                    symbol,
                    remove_legacy=False,
                )
                metrics.append(result)
                if result.failed_rows == 0:
                    successful_pairs.append((date_str, symbol))

        delete_legacy(successful_pairs)
        for session_date in month_dates:
            repair(session_date)
            mark_repair_done(session_date.isoformat())

    failures = sum(metric.failed_rows for metric in metrics)
    if failures:
        log.error(
            "After-session backfill incomplete: %d failed row(s); legacy pairs will retry",
            failures,
        )
        return metrics

    mark_done(run_date)
    log.info("After-session backfill complete for scheduler date %s", run_date)
    return metrics


def serve() -> None:
    poll_seconds = config.reconciler.poll_seconds
    if poll_seconds <= 0:
        raise ValueError("RECONCILER_POLL_SECONDS must be greater than zero")

    log.info(
        "After-session scheduler ready: weekdays %02d:%02d %s; "
        "scope=all historical legacy pairs plus latest-session symbols; poll=%.0fs",
        config.reconciler.reconciler_hour,
        config.reconciler.reconciler_minute,
        config.tick_sync.session_tz,
        poll_seconds,
    )
    while True:
        try:
            run_due_backfill()
        except Exception:
            log.exception("After-session scheduler iteration failed; will retry")
        time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="bypass weekday, trigger-time, and completion-state guards",
    )
    args = parser.parse_args()
    if args.force and not args.once:
        parser.error("--force requires --once")
    if not args.once:
        serve()
        return

    metrics = run_due_backfill(force=args.force)
    if metrics is not None and any(metric.failed_rows for metric in metrics):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
