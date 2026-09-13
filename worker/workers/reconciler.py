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
from model import (
    LEGACY_EVENT_SEQUENCE_START,
    TICKS_CLICKHOUSE_ORDER_BY,
    TICKS_CLICKHOUSE_TABLE,
)
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


def _tick_key(row: dict) -> tuple[str, str, str, int, str]:
    sending_time = _to_utc_rounded_microseconds(row["sending_time"])
    return (
        str(row["symbol"]),
        sending_time.date().isoformat(),
        str(row.get("board_id") or ""),
        int(row["event_sequence"]),
        sending_time.isoformat(timespec="microseconds"),
    )


def _tick_values(row: dict) -> tuple[str, float, int, int]:
    return (
        _to_utc_rounded_microseconds(row["sending_time"]).isoformat(
            timespec="microseconds"
        ),
        float(row["match_price"]),
        int(row["match_qty"]),
        int(row["side"]),
    )


def fetch_session_ticks(date_str: str, symbol: str | None = None) -> list[dict]:
    day = date.fromisoformat(date_str)
    target_symbol = symbol or symbol_for_date(day)
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
    SELECT
        symbol,
        sending_time,
        match_price,
        match_qty,
        side,
        board_id,
        event_sequence
    FROM {db}.{TICKS_CLICKHOUSE_TABLE} FINAL
    WHERE symbol = '{symbol_escaped}'
      AND toDate(sending_time) = '{date_str}'
      AND sending_time >= toDateTime64('{session_start_utc.strftime("%Y-%m-%d %H:%M:%S")}', 6, 'UTC')
      AND sending_time <= toDateTime64('{session_end_utc.strftime("%Y-%m-%d %H:%M:%S")}', 6, 'UTC')
    """.strip()

    result = ch_client.query(sql)
    return [
        {
            "symbol": row[0],
            "sending_time": row[1],
            "match_price": row[2],
            "match_qty": row[3],
            "side": row[4],
            "board_id": row[5],
            "event_sequence": row[6],
            "received_at": None,
        }
        for row in result.result_rows
    ]


def diff_ticks(
    api_rows: list[dict], ch_rows: list[dict]
) -> tuple[list[dict], list[dict]]:
    api_by_key: dict[tuple[str, str, str, int, str], dict] = {}
    for row in api_rows:
        key = _tick_key(row)
        prior = api_by_key.get(key)
        if prior is None:
            api_by_key[key] = row
            continue
        if _tick_values(prior) == _tick_values(row):
            continue

        prior_has_side = int(prior["side"]) in (1, 2)
        row_has_side = int(row["side"]) in (1, 2)
        if row_has_side and not prior_has_side:
            api_by_key[key] = row
        elif prior_has_side and not row_has_side:
            continue
        else:
            raise ValueError(f"DNSE returned conflicting event identity: {key}")

    ch_by_key = {
        _tick_key(row): row
        for row in ch_rows
        if int(row["event_sequence"]) < LEGACY_EVENT_SEQUENCE_START
    }
    missing = [row for key, row in api_by_key.items() if key not in ch_by_key]
    drift = [
        row
        for key, row in api_by_key.items()
        if key in ch_by_key and _tick_values(row) != _tick_values(ch_by_key[key])
    ]
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
                "board_id",
                "event_sequence",
            ],
        )
        log.info("Patched %d rows into %s", len(insert_rows), table)
        return len(insert_rows), 0
    except Exception:
        log.exception("ClickHouse insert failed for %d rows", len(insert_rows))
        return 0, len(insert_rows)


def _assert_event_identity_schema(ch_client, db: str) -> None:
    row = ch_client.query(
        f"""
        SELECT engine, sorting_key
        FROM system.tables
        WHERE database = '{db}' AND name = '{TICKS_CLICKHOUSE_TABLE}'
        """
    ).result_rows
    if not row:
        raise RuntimeError(f"{db}.{TICKS_CLICKHOUSE_TABLE} does not exist")
    engine, sorting_key = row[0]
    normalized_key = str(sorting_key).replace(" ", "")
    expected_key = TICKS_CLICKHOUSE_ORDER_BY.replace(" ", "")
    if engine != "ReplacingMergeTree" or normalized_key != expected_key:
        raise RuntimeError(
            f"{db}.{TICKS_CLICKHOUSE_TABLE} still uses sorting key {sorting_key!r}; "
            "run scripts/migrate_ticks_event_identity.py --swap before reconciliation"
        )


def _delete_legacy_rows(
    ch_client, db: str, symbol: str, date_str: str, *, dry_run: bool
) -> int:
    session_start_utc, session_end_utc = _session_window_utc(date_str)
    symbol_escaped = symbol.replace("'", "''")
    where = (
        f"symbol = '{symbol_escaped}' "
        f"AND sending_time >= toDateTime64('{session_start_utc.strftime('%Y-%m-%d %H:%M:%S')}', 6, 'UTC') "
        f"AND sending_time <= toDateTime64('{session_end_utc.strftime('%Y-%m-%d %H:%M:%S')}', 6, 'UTC') "
        f"AND event_sequence >= {LEGACY_EVENT_SEQUENCE_START}"
    )
    count = int(
        ch_client.query(
            f"SELECT count() FROM {db}.{TICKS_CLICKHOUSE_TABLE} FINAL WHERE {where}"
        ).result_rows[0][0]
    )
    if count and not dry_run:
        ch_client.client.command(
            f"ALTER TABLE {db}.{TICKS_CLICKHOUSE_TABLE} DELETE WHERE {where}",
            settings={"mutations_sync": 2},
        )
        log.info("Removed %d migrated legacy rows for %s on %s", count, symbol, date_str)
    return count

def delete_legacy_pairs(pairs: list[tuple[str, str]]) -> int:
    """Delete successful legacy pairs in one mutation per calendar month."""
    if not pairs:
        return 0

    ch_client = get_clickhouse_client()
    db = config.clickhouse.database
    _assert_event_identity_schema(ch_client, db)
    pairs_by_month: dict[str, dict[str, set[str]]] = {}
    for date_str, symbol in pairs:
        month = date_str[:7]
        symbols_by_date = pairs_by_month.setdefault(month, {})
        symbols_by_date.setdefault(date_str, set()).add(symbol)

    removed = 0
    for month, symbols_by_date in sorted(pairs_by_month.items()):
        date_clauses: list[str] = []
        for date_str, symbols in sorted(symbols_by_date.items()):
            session_start_utc, session_end_utc = _session_window_utc(date_str)
            escaped_symbols = (
                symbol.replace("'", "''") for symbol in sorted(symbols)
            )
            symbol_list = ", ".join(f"'{symbol}'" for symbol in escaped_symbols)
            date_clauses.append(
                "("
                f"sending_time >= toDateTime64('{session_start_utc.strftime('%Y-%m-%d %H:%M:%S')}', 6, 'UTC') "
                f"AND sending_time <= toDateTime64('{session_end_utc.strftime('%Y-%m-%d %H:%M:%S')}', 6, 'UTC') "
                f"AND symbol IN ({symbol_list})"
                ")"
            )

        where = (
            f"event_sequence >= {LEGACY_EVENT_SEQUENCE_START} "
            f"AND ({' OR '.join(date_clauses)})"
        )
        count = int(
            ch_client.query(
                f"SELECT count() FROM {db}.{TICKS_CLICKHOUSE_TABLE} FINAL WHERE {where}"
            ).result_rows[0][0]
        )
        if count:
            ch_client.client.command(
                f"ALTER TABLE {db}.{TICKS_CLICKHOUSE_TABLE} DELETE WHERE {where}",
                settings={"mutations_sync": 2},
            )
            removed += count
            log.info("Removed %d migrated legacy rows for %s", count, month)

    return removed


def run_reconciler(
    date_str: str,
    dry_run: bool = False,
    symbol: str | None = None,
    *,
    remove_legacy: bool = True,
) -> ReconcilerMetrics:
    started = time.monotonic()
    metrics = ReconcilerMetrics(run_date=date_str)
    target_symbol = symbol or symbol_for_date(date.fromisoformat(date_str))

    try:
        ch_client = get_clickhouse_client()
        _assert_event_identity_schema(ch_client, config.clickhouse.database)
        try:
            api_rows = fetch_session_ticks(date_str, symbol=target_symbol)
        except requests.RequestException as exc:
            log.error("Request failed for %s: %s — retrying once", date_str, exc)
            time.sleep(5)
            api_rows = fetch_session_ticks(date_str, symbol=target_symbol)

        metrics.fetched_rows = len(api_rows)
        ch_rows = fetch_ch_session_ticks(
            ch_client=ch_client,
            db=config.clickhouse.database,
            symbol=target_symbol,
            date_str=date_str,
        )

        if ch_rows and not api_rows:
            log.error(
                "DNSE returned no authoritative rows for %s %s while ClickHouse "
                "contains %d row(s); preserving existing data for retry",
                target_symbol,
                date_str,
                len(ch_rows),
            )
            metrics.failed_rows = 1
            return metrics

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

        if failed_rows == 0 and remove_legacy:
            _delete_legacy_rows(
                ch_client,
                config.clickhouse.database,
                target_symbol,
                date_str,
                dry_run=dry_run,
            )

    except Exception as exc:
        log.exception("Reconciler failed for %s %s: %s", target_symbol, date_str, exc)
        metrics.failed_rows = max(1, metrics.failed_rows, metrics.mismatches_missing)
    finally:
        metrics.duration_s = time.monotonic() - started

    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--symbol")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not should_run_today(
        tz_name=config.tick_sync.session_tz,
        trigger_hour=config.reconciler.reconciler_hour,
        trigger_minute=config.reconciler.reconciler_minute,
        force=args.force,
    ):
        log.info("Schedule guard: not running")
        return

    metrics = run_reconciler(
        args.date,
        dry_run=args.dry_run,
        symbol=args.symbol.upper() if args.symbol else None,
    )
    print_metrics(metrics)

    if not args.dry_run and metrics.failed_rows == 0:
        mark_run_done(args.date)


# ---------------------------------------------------------------------------
# Prefect tasks
# ---------------------------------------------------------------------------


@task(log_prints=True)
def reconcile_date(
    date_str: str, dry_run: bool = False, symbol: str | None = None
) -> ReconcilerMetrics:
    metrics = run_reconciler(date_str, dry_run=dry_run, symbol=symbol)
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
    symbol: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    if session_date is None:
        session_date = date.today().isoformat()
    if not should_run_today(
        tz_name=config.tick_sync.session_tz,
        trigger_hour=config.reconciler.reconciler_hour,
        trigger_minute=config.reconciler.reconciler_minute,
        force=force,
    ):
        log.info("Schedule guard: not running")
        return
    metrics = reconcile_date(session_date, dry_run=dry_run, symbol=symbol)
    if not dry_run and metrics.failed_rows == 0:
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


if __name__ == "__main__":
    main()
