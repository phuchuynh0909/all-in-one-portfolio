"""Block-episode ("large-execution footprint") daily reconciler.

Batch/offline counterpart to Phase 3's streaming detector, and the authoritative
source of ``block_episodes``. For each watchlist symbol it pulls the day's full
tape from the same DNSE GraphQL API as the large-order reconciler, keeps only
in-session non-auction trades, runs the statistical detector in
``core.large_execution`` (rolling flow, prior-only signed-notional z-scores,
large prints, and stitched same-direction episodes), and upserts one row per
episode into ClickHouse ``block_episodes``.

The symbol scope, session window, board and request pacing are shared with the
large-order pipeline (``config.large_order``); the detection knobs and output
table come from ``config.block_episode``.

Because an episode's bounds/aggregates can change as more of the day's tape
arrives, this reconciler is authoritative: it upserts *all* of the day's
episodes (ReplacingMergeTree overwrites by ``(symbol, start_time, side)``) and
reports how many keys were new.

Run:
    python workers/block_episode_reconciler.py                 # respects schedule guard
    python workers/block_episode_reconciler.py --force         # bypass guard
    python workers/block_episode_reconciler.py --date 2026-06-20 --dry-run
    python workers/block_episode_reconciler.py --symbol FPT --symbol HPG
    # Date-range backfill (weekends skipped; bypasses the schedule guard):
    python workers/block_episode_reconciler.py --from-date 2026-06-01 --to-date 2026-06-10
    python workers/block_episode_reconciler.py --from-date 2026-06-01   # --to-date defaults to today
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Allow `python workers/block_episode_reconciler.py` (script-by-path) as well as
# `python -m workers.block_episode_reconciler` by ensuring the worker root is on
# sys.path before importing the infra/config/core packages.
_WORKER_ROOT = str(Path(__file__).resolve().parent.parent)
if _WORKER_ROOT not in sys.path:
    sys.path.insert(0, _WORKER_ROOT)

import requests

from infra.clickhouse_client import get_clickhouse_client
from config import config
from infra.dnse_client import DNSEClient
from model import (
    BLOCK_EPISODES_CLICKHOUSE_TABLE,
    BLOCK_EPISODES_CREATE_TABLE_DDL,
    BLOCK_EPISODES_COLUMNS,
)
from core.tick_contract import normalize_tick
from core.large_order import is_auction_time
from core.large_execution import detect, to_episode_row
from core.watchlist import load_symbols

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# Independent run-state file so this reconciler never clobbers the others.
STATE_FILE = (
    Path(__file__).parent.parent / "state_dir" / "block_episode_reconciler_run_state.json"
)


# ---------------------------------------------------------------------------
# Schedule guard (mirrors the large-order reconciler, separate state file)
# ---------------------------------------------------------------------------
def should_run_today(force: bool = False, trigger_hour: int = 15) -> bool:
    tz = ZoneInfo(config.large_order.session_tz)
    now = datetime.now(tz)
    if force:
        log.info("force=True: bypassing schedule guard")
        return True
    if now.hour < trigger_hour:
        log.debug("Before trigger hour %d (now %02d:%02d)", trigger_hour, now.hour, now.minute)
        return False
    today_str = now.date().isoformat()
    if STATE_FILE.exists():
        try:
            if json.loads(STATE_FILE.read_text()).get("last_run_date") == today_str:
                log.info("Already ran today (%s), skipping", today_str)
                return False
        except (json.JSONDecodeError, IOError):
            pass
    return True


def mark_run_done(date_str: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(
            {
                "last_run_date": date_str,
                "last_run_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )
    )
    log.info("Marked block-episode reconciler run done for %s", date_str)


# ---------------------------------------------------------------------------
# Time / key helpers
# ---------------------------------------------------------------------------
def _session_window_utc(date_str: str) -> tuple[datetime, datetime]:
    day = date.fromisoformat(date_str)
    session_tz = ZoneInfo(config.large_order.session_tz)
    start_local = datetime.combine(day, config.large_order.session_start, tzinfo=session_tz)
    end_local = datetime.combine(day, config.large_order.session_end, tzinfo=session_tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _to_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _episode_key(row: dict) -> tuple[str, str, int]:
    """Dedup key of an episode: (symbol, start_time_iso, side)."""
    return (
        str(row["symbol"]),
        _to_utc(row["start_time"]).isoformat(timespec="microseconds"),
        int(row["side"]),
    )


# ---------------------------------------------------------------------------
# Fetch / detect / upsert
# ---------------------------------------------------------------------------
def fetch_session_ticks(symbol: str, date_str: str, client: DNSEClient) -> list[dict]:
    """Fetch the day's tape for *symbol*, normalized and filtered to the
    in-session, non-auction continuous trades the detector should see."""
    day = date.fromisoformat(date_str)
    session_start_utc, session_end_utc = _session_window_utc(date_str)
    tz = config.large_order.session_tz
    auction_windows = config.large_order.auction_windows

    raw_ticks = client.fetch_day_ticks(
        symbol=symbol, day=day, board=config.large_order.board
    )

    in_session: list[dict] = []
    for raw in raw_ticks:
        tick = normalize_tick(raw)
        if tick is None:
            continue
        sending_time = _to_utc(tick["sending_time"])
        if not (session_start_utc <= sending_time <= session_end_utc):
            continue
        if is_auction_time(sending_time, tz, auction_windows):
            continue  # drop ATO/ATC auction prints
        tick["sending_time"] = sending_time
        in_session.append(tick)

    return in_session


def detect_episodes(ticks: list[dict]) -> list[dict]:
    """Run the large-execution detector over one symbol's in-session tape."""
    return detect(ticks, config.block_episode.detection_params)["episodes"]


def fetch_ch_episode_keys(ch_client, db: str, symbol: str, date_str: str) -> set[tuple]:
    session_start_utc, session_end_utc = _session_window_utc(date_str)
    symbol_escaped = symbol.replace("'", "''")
    table = config.block_episode.table
    sql = f"""
    SELECT symbol, start_time, side
    FROM {db}.{table} FINAL
    WHERE symbol = '{symbol_escaped}'
      AND start_time >= toDateTime64('{session_start_utc.strftime("%Y-%m-%d %H:%M:%S")}', 6, 'UTC')
      AND start_time <= toDateTime64('{session_end_utc.strftime("%Y-%m-%d %H:%M:%S")}', 6, 'UTC')
    """.strip()
    result = ch_client.query(sql)
    return {
        _episode_key({"symbol": r[0], "start_time": r[1], "side": r[2]})
        for r in result.result_rows
    }


def upsert_episodes(ch_client, db: str, episodes: list[dict], dry_run: bool = False) -> tuple[int, int]:
    """Insert all episodes (ReplacingMergeTree dedups/overwrites by key)."""
    if not episodes:
        return 0, 0
    now_utc = datetime.now(timezone.utc)
    insert_rows = [to_episode_row(ep, now_utc) for ep in episodes]
    table = f"{db}.{config.block_episode.table}"

    if dry_run:
        log.info("Dry-run: would upsert %d episodes into %s", len(insert_rows), table)
        return len(insert_rows), 0

    try:
        ch_client.client.insert(
            table, insert_rows, column_names=BLOCK_EPISODES_COLUMNS
        )
        log.info("Upserted %d episodes into %s", len(insert_rows), table)
        return len(insert_rows), 0
    except Exception:
        log.exception("ClickHouse insert failed for %d episodes", len(insert_rows))
        return 0, len(insert_rows)


def _ensure_table(ch_client, db: str) -> None:
    ch_client.query(BLOCK_EPISODES_CREATE_TABLE_DDL.format(database=db))


@dataclass
class BlockEpisodeReconcilerMetrics:
    run_date: str
    symbols: int = 0
    episodes: int = 0        # episodes detected from the API tape
    new_episodes: int = 0    # episodes whose key was absent in ClickHouse
    upserted_rows: int = 0
    skipped_rows: int = 0    # dry-run only
    failed_rows: int = 0
    failed_symbols: list[str] = field(default_factory=list)
    duration_s: float = 0.0


def run_reconciler(
    date_str: str,
    symbols: list[str] | None = None,
    dry_run: bool = False,
) -> BlockEpisodeReconcilerMetrics:
    started = time.monotonic()
    if symbols is None:
        symbols = load_symbols(config.large_order.watchlist_file)
    metrics = BlockEpisodeReconcilerMetrics(run_date=date_str, symbols=len(symbols))

    if not symbols:
        log.warning("No symbols to reconcile (watchlist empty)")
        metrics.duration_s = time.monotonic() - started
        return metrics

    db = config.clickhouse.database
    ch_client = get_clickhouse_client()
    _ensure_table(ch_client, db)
    client = DNSEClient(request_delay=config.large_order.request_delay, timeout=30, logger=log)

    for idx, symbol in enumerate(symbols, 1):
        try:
            try:
                ticks = fetch_session_ticks(symbol, date_str, client)
            except requests.RequestException as exc:
                log.error("Request failed for %s (%s): %s — retrying once", symbol, date_str, exc)
                time.sleep(5)
                ticks = fetch_session_ticks(symbol, date_str, client)

            episodes = detect_episodes(ticks)
            metrics.episodes += len(episodes)

            ch_keys = fetch_ch_episode_keys(ch_client, db, symbol, date_str)
            new_count = sum(1 for e in episodes if _episode_key(e) not in ch_keys)
            metrics.new_episodes += new_count

            upserted, failed = upsert_episodes(ch_client, db, episodes, dry_run=dry_run)
            if dry_run:
                metrics.skipped_rows += upserted
            else:
                metrics.upserted_rows += upserted
            metrics.failed_rows += failed

            log.info(
                "[%d/%d] %s — %d episodes, %d new%s",
                idx, len(symbols), symbol, len(episodes), new_count,
                " (dry-run)" if dry_run else "",
            )
        except Exception as exc:
            log.exception("Reconcile failed for %s: %s", symbol, exc)
            metrics.failed_symbols.append(symbol)

    metrics.duration_s = time.monotonic() - started
    return metrics


def _print_metrics(m: BlockEpisodeReconcilerMetrics) -> None:
    print(
        f"\n{'=' * 56}\n"
        f"  Block-Episode Reconciler  —  {m.run_date}\n"
        f"{'=' * 56}\n"
        f"  Symbols            : {m.symbols:>12,}\n"
        f"  Episodes           : {m.episodes:>12,}\n"
        f"  New episodes       : {m.new_episodes:>12,}\n"
        f"  Upserted rows      : {m.upserted_rows:>12,}\n"
        f"  Skipped (dry-run)  : {m.skipped_rows:>12,}\n"
        f"  Failed rows        : {m.failed_rows:>12,}\n"
        f"  Failed symbols     : {len(m.failed_symbols):>12,}\n"
        f"  Duration (s)       : {m.duration_s:>12.2f}\n"
        f"{'=' * 56}"
    )
    if m.failed_symbols:
        log.warning("Failed symbols: %s", ", ".join(m.failed_symbols))


def weekdays_in_range(from_date: date, to_date: date) -> list[date]:
    """Trading-day candidates in [from_date, to_date] — Mon–Fri only.

    Weekends are skipped; market holidays still hit the API and simply return
    no ticks (0 episodes), so they're harmless.
    """
    days: list[date] = []
    cur = from_date
    while cur <= to_date:
        if cur.weekday() < 5:  # Mon=0 .. Fri=4
            days.append(cur)
        cur += timedelta(days=1)
    return days


def run_range(from_date: date, to_date: date, symbols, dry_run: bool) -> None:
    """Backfill every weekday in [from_date, to_date]."""
    days = weekdays_in_range(from_date, to_date)
    log.info(
        "Backfill range %s .. %s — %d weekday(s)%s",
        from_date, to_date, len(days), " (dry-run)" if dry_run else "",
    )
    if not days:
        log.warning("No weekdays in range %s .. %s", from_date, to_date)
        return

    grand = BlockEpisodeReconcilerMetrics(run_date=f"{from_date}..{to_date}")
    for d in days:
        m = run_reconciler(d.isoformat(), symbols=symbols, dry_run=dry_run)
        _print_metrics(m)
        grand.episodes += m.episodes
        grand.new_episodes += m.new_episodes
        grand.upserted_rows += m.upserted_rows
        grand.skipped_rows += m.skipped_rows
        grand.failed_rows += m.failed_rows
        grand.failed_symbols.extend(m.failed_symbols)
        grand.duration_s += m.duration_s
        grand.symbols = m.symbols

    print(f"\n### Range total — {len(days)} weekday(s) ###")
    _print_metrics(grand)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat(),
                        help="Single session date (default today). Ignored if --from-date is set.")
    parser.add_argument("--from-date", dest="from_date",
                        help="Start of a date range to backfill (inclusive). Weekends are skipped.")
    parser.add_argument("--to-date", dest="to_date",
                        help="End of the date range (inclusive). Defaults to today.")
    parser.add_argument("--symbol", action="append", dest="symbols",
                        help="Reconcile only this symbol (repeatable). Defaults to the watchlist.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Range mode: explicit manual backfill — bypasses the daily schedule guard.
    if args.from_date or args.to_date:
        if not args.from_date:
            parser.error("--to-date requires --from-date")
        from_d = date.fromisoformat(args.from_date)
        to_d = date.fromisoformat(args.to_date) if args.to_date else date.today()
        if to_d < from_d:
            parser.error(f"--to-date ({to_d}) is before --from-date ({from_d})")
        run_range(from_d, to_d, symbols=args.symbols, dry_run=args.dry_run)
        return

    # Single-day mode: respects the once-per-day schedule guard.
    if not should_run_today(force=args.force):
        log.info("Schedule guard: not running")
        return

    metrics = run_reconciler(args.date, symbols=args.symbols, dry_run=args.dry_run)
    _print_metrics(metrics)

    if not args.dry_run:
        mark_run_done(args.date)


if __name__ == "__main__":
    main()
