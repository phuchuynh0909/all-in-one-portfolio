from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from infra.audit_queries import ReconcilerMetrics, print_metrics
from infra.clickhouse_client import get_clickhouse_client
from config import config
from infra.dnse_client import DNSEClient
from model import TICKS_CLICKHOUSE_TABLE
from prefect import flow, task
from infra.reconciler_schedule import mark_run_done, should_run_today
from core.tick_contract import normalize_tick, to_clickhouse_tuple
from core.vn30f_symbol import symbol_for_date

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)


def _session_window_utc(date_str: str) -> tuple[datetime, datetime]:
    day = date.fromisoformat(date_str)
    session_tz = ZoneInfo(config.tick_sync.session_tz)
    session_start_local = datetime.combine(
        day, config.tick_sync.session_start, tzinfo=session_tz
    )
    session_end_local = datetime.combine(
        day, config.tick_sync.session_end, tzinfo=session_tz
    )
    return (
        session_start_local.astimezone(timezone.utc),
        session_end_local.astimezone(timezone.utc),
    )


def _to_utc_rounded_microseconds(value: datetime | str) -> datetime:
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        dt = value

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    return datetime.fromtimestamp(round(dt.timestamp(), 6), tz=timezone.utc)


def _tick_key(row: dict) -> tuple[str, str, float, int, int]:
    return (
        str(row["symbol"]),
        _to_utc_rounded_microseconds(row["sending_time"]).isoformat(
            timespec="microseconds"
        ),
        float(row["match_price"]),
        int(row["match_qty"]),
        int(row["side"]),
    )


def fetch_session_ticks(date_str: str) -> list[dict]:
    day = date.fromisoformat(date_str)
    target_symbol = symbol_for_date(day)
    session_start_utc, session_end_utc = _session_window_utc(date_str)
    client = DNSEClient(
        request_delay=config.reconciler.request_delay, timeout=30, logger=log
    )

    raw_ticks = client.fetch_day_ticks(
        symbol=target_symbol,
        day=day,
        board=config.tick_sync.board,
    )

    canonical_rows: list[dict] = []
    for raw in raw_ticks:
        normalized = normalize_tick(raw)
        if normalized is None:
            continue

        sending_time = _to_utc_rounded_microseconds(normalized["sending_time"])
        if session_start_utc <= sending_time <= session_end_utc:
            normalized["sending_time"] = sending_time
            canonical_rows.append(normalized)

    return canonical_rows


def fetch_ch_session_ticks(
    ch_client, db: str, symbol: str, date_str: str
) -> list[dict]:
    session_start_utc, session_end_utc = _session_window_utc(date_str)

    symbol_escaped = symbol.replace("'", "''")

    sql = f"""
    SELECT symbol, sending_time, match_price, match_qty, side
    FROM {db}.{TICKS_CLICKHOUSE_TABLE} FINAL
    WHERE symbol = '{symbol_escaped}'
      AND sending_time >= toDateTime64('{session_start_utc.strftime("%Y-%m-%d %H:%M:%S")}', 6, 'UTC')
      AND sending_time <= toDateTime64('{session_end_utc.strftime("%Y-%m-%d %H:%M:%S")}', 6, 'UTC')
    """.strip()

    result = ch_client.query(sql)
    rows: list[dict] = []
    for row in result.result_rows:
        rows.append(
            {
                "symbol": row[0],
                "sending_time": row[1],
                "match_price": row[2],
                "match_qty": row[3],
                "side": row[4],
                "received_at": None,
            }
        )
    return rows


def diff_ticks(
    api_rows: list[dict], ch_rows: list[dict]
) -> tuple[list[dict], list[dict]]:
    ch_keys = {_tick_key(row) for row in ch_rows}
    missing = [row for row in api_rows if _tick_key(row) not in ch_keys]
    drift: list[dict] = []
    return missing, drift


def patch_ticks(
    ch_client,
    db: str,
    missing: list[dict],
    drift: list[dict] | None = None,
    dry_run: bool = False,
) -> tuple[int, int]:
    drift_rows = drift or []
    patch_rows = [*missing, *drift_rows]

    if not patch_rows:
        return 0, 0

    now_utc = datetime.now(timezone.utc)
    insert_rows = []
    for row in patch_rows:
        patched_row = dict(row)
        patched_row["received_at"] = now_utc
        insert_rows.append(to_clickhouse_tuple(patched_row))

    if dry_run:
        log.info(
            "Dry-run: would patch %d rows into %s.%s",
            len(insert_rows),
            db,
            TICKS_CLICKHOUSE_TABLE,
        )
        return len(insert_rows), 0

    table = f"{db}.{TICKS_CLICKHOUSE_TABLE}"
    try:
        ch_client.client.insert(
            table,
            insert_rows,
            column_names=[
                "symbol",
                "sending_time",
                "match_price",
                "match_qty",
                "side",
                "received_at",
            ],
        )
        log.info("Patched %d rows into %s", len(insert_rows), table)
        return len(insert_rows), 0
    except Exception:
        log.exception("ClickHouse insert failed for %d rows", len(insert_rows))
        return 0, len(insert_rows)


def run_reconciler(date_str: str, dry_run: bool = False) -> ReconcilerMetrics:
    started = time.monotonic()
    metrics = ReconcilerMetrics(run_date=date_str)

    try:
        try:
            api_rows = fetch_session_ticks(date_str)
        except requests.RequestException as exc:
            log.error("Request failed for %s: %s — retrying once", date_str, exc)
            time.sleep(5)
            api_rows = fetch_session_ticks(date_str)

        metrics.fetched_rows = len(api_rows)

        ch_client = get_clickhouse_client()
        target_symbol = symbol_for_date(date.fromisoformat(date_str))
        ch_rows = fetch_ch_session_ticks(
            ch_client=ch_client,
            db=config.clickhouse.database,
            symbol=target_symbol,
            date_str=date_str,
        )

        missing, drift = diff_ticks(api_rows, ch_rows)
        metrics.mismatches_missing = len(missing)
        metrics.mismatches_drift = len(drift)

        patched_rows, failed_rows = patch_ticks(
            ch_client=ch_client,
            db=config.clickhouse.database,
            missing=missing,
            drift=drift,
            dry_run=dry_run,
        )

        if dry_run:
            metrics.skipped_rows = patched_rows
        else:
            metrics.patched_rows = patched_rows
        metrics.failed_rows += failed_rows

    except Exception as exc:
        log.exception("Reconciler failed for %s: %s", date_str, exc)
        metrics.failed_rows = max(metrics.failed_rows, metrics.mismatches_missing)
    finally:
        metrics.duration_s = time.monotonic() - started

    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not should_run_today(force=args.force):
        log.info("Schedule guard: not running")
        return

    metrics = run_reconciler(args.date, dry_run=args.dry_run)
    print_metrics(metrics)

    if not args.dry_run:
        mark_run_done(args.date)


# ---------------------------------------------------------------------------
# Prefect tasks
# ---------------------------------------------------------------------------


@task(log_prints=True)
def reconcile_date(date_str: str, dry_run: bool = False) -> ReconcilerMetrics:
    metrics = run_reconciler(date_str, dry_run=dry_run)
    print_metrics(metrics)
    return metrics


@task(log_prints=True)
def backfill_dates(
    start_date: str,
    end_date: str,
    dry_run: bool = False,
) -> list[ReconcilerMetrics]:
    results: list[ReconcilerMetrics] = []
    current = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    while current <= end:
        date_str = current.isoformat()
        log.info("Backfilling %s ...", date_str)
        metrics = run_reconciler(date_str, dry_run=dry_run)
        print_metrics(metrics)
        results.append(metrics)
        current += timedelta(days=1)
    return results


# ---------------------------------------------------------------------------
# Prefect flows
# ---------------------------------------------------------------------------


@flow(log_prints=True)
def reconciler_pipeline(
    session_date: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    if session_date is None:
        session_date = date.today().isoformat()
    if not should_run_today(force=force):
        log.info("Schedule guard: not running")
        return
    metrics = reconcile_date(session_date, dry_run=dry_run)
    if not dry_run:
        mark_run_done(session_date)
    log.info("reconciler_pipeline complete for %s", session_date)


@flow(log_prints=True)
def backfill_pipeline(
    start_date: str,
    end_date: str | None = None,
    dry_run: bool = False,
) -> None:
    if end_date is None:
        end_date = date.today().isoformat()
    all_metrics = backfill_dates(start_date, end_date, dry_run=dry_run)
    total_fetched = sum(m.fetched_rows for m in all_metrics)
    total_patched = sum(m.patched_rows for m in all_metrics)
    print(
        f"Backfill complete — {len(all_metrics)} days, "
        f"{total_fetched} fetched, {total_patched} patched"
    )


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from pathlib import Path

    # CLI mode: python reconciler.py [--date ...] [--force] [--dry-run]
    if len(sys.argv) > 1 and not sys.argv[1].startswith("deploy"):
        main()
    else:
        reconciler_pipeline.from_source(
            source=str(Path(__file__).parent.parent),
            entrypoint="workers/reconciler.py:reconciler_pipeline",
        ).deploy(
            name="tick-reconciler-daily",
            work_pool_name="my-worker",
            cron="5 8 * * 1-5",  # 08:05 UTC = 15:05 ICT, weekdays
        )

        backfill_pipeline.from_source(
            source=str(Path(__file__).parent.parent),
            entrypoint="workers/reconciler.py:backfill_pipeline",
        ).deploy(
            name="tick-reconciler-backfill",
            work_pool_name="my-worker",
            # No cron — triggered manually
        )
