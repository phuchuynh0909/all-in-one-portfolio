"""Prefect flow: aggregate tick data into 5-minute OHLC candles."""

from typing import Any, Generator, Sequence
from datetime import date, datetime, timedelta
from prefect import flow, task

import os
import json
import math
import clickhouse_connect  # type: ignore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SYNC_STATE_PATH = os.getenv(
    "OHLC_5M_SYNC_STATE_PATH", "./.state/ohlc_5m_sync_state.json"
)
TICKS_TABLE = os.getenv("CLICKHOUSE_TICKS_TABLE", "ticks")
OHLC_5M_TABLE = os.getenv("CLICKHOUSE_OHLC_5M_TABLE", "ohlc_5m")

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _get_env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _get_ch_client():
    host = _get_env("CLICKHOUSE_HOST", "192.168.1.3")
    port = int(_get_env("CLICKHOUSE_PORT", "8123"))
    username = _get_env("CLICKHOUSE_USER", "kyostyle1")
    password = _get_env("CLICKHOUSE_PASSWORD", "kyostyle1")
    database = _get_env("CLICKHOUSE_DB", "default")
    return clickhouse_connect.get_client(
        host=host,
        port=port,
        username=username,
        password=password,
        database=database,
    )


def _load_sync_state(state_path: str) -> dict[str, Any]:
    if not os.path.exists(state_path):
        return {}
    with open(state_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_sync_state(state_path: str, state: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f)


def _iter_batches(
    rows: Sequence[tuple], batch_size: int = 50000
) -> Generator[list[tuple], None, None]:
    total = len(rows)
    if total == 0:
        return
    num_batches = math.ceil(total / batch_size)
    for i in range(num_batches):
        start = i * batch_size
        end = min(start + batch_size, total)
        yield list(rows[start:end])


def _ensure_ohlc_5m_table_exists(client, database: str, table: str) -> None:
    client.command(f"CREATE DATABASE IF NOT EXISTS {database}")
    client.command(
        f"""
        CREATE TABLE IF NOT EXISTS {database}.{table} (
            symbol String,
            ts DateTime('Asia/Ho_Chi_Minh'),
            open Float64,
            high Float64,
            low Float64,
            close Float64,
            volume Int64,
            buy_volume Int64,
            sell_volume Int64,
            ver DateTime64(3) DEFAULT now64(3)
        )
        ENGINE = ReplacingMergeTree(ver)
        PARTITION BY toYYYYMM(ts)
        ORDER BY (symbol, ts)
        """
    )


# ---------------------------------------------------------------------------
# Prefect tasks
# ---------------------------------------------------------------------------


VN30F1M = "VN30F1M"


@task(log_prints=True)
def aggregate_ticks_to_ohlc_5m(
    date_from: str,
    date_to: str,
    contract_symbol: str | None = None,
) -> int:
    database = _get_env("CLICKHOUSE_DB", "default")
    client = _get_ch_client()
    _ensure_ohlc_5m_table_exists(client, database, OHLC_5M_TABLE)

    if contract_symbol:
        symbol_filter = f"AND symbol = '{contract_symbol}'"
    else:
        symbol_filter = "AND (symbol LIKE '41I1%' OR symbol LIKE 'VN30%')"

    sql = f"""
        SELECT
            '{VN30F1M}',
            toStartOfFiveMinutes(toTimezone(sending_time, 'Asia/Ho_Chi_Minh')) AS ts,
            argMin(match_price, sending_time) AS open,
            max(match_price)                  AS high,
            min(match_price)                  AS low,
            argMax(match_price, sending_time) AS close,
            sum(match_qty)                    AS volume,
            sumIf(match_qty, side = 1)        AS buy_volume,
            sumIf(match_qty, side = 2)        AS sell_volume
        FROM {database}.{TICKS_TABLE} FINAL
        WHERE sending_time >= toDateTime64('{date_from}', 6, 'UTC')
          AND sending_time <= toDateTime64('{date_to}', 6, 'UTC')
          {symbol_filter}
        GROUP BY ts
        ORDER BY ts
    """

    raw_rows = client.query(sql).result_rows

    column_names = [
        "symbol",
        "ts",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "buy_volume",
        "sell_volume",
    ]

    rows: list[tuple] = [
        (
            VN30F1M,
            r[1] if isinstance(r[1], datetime) else datetime.fromisoformat(str(r[1])),
            float(r[2]),
            float(r[3]),
            float(r[4]),
            float(r[5]),
            int(r[6]),
            int(r[7]),
            int(r[8]),
        )
        for r in raw_rows
    ]

    inserted = 0
    for batch in _iter_batches(rows, batch_size=50000):
        client.insert(f"{database}.{OHLC_5M_TABLE}", batch, column_names=column_names)
        inserted += len(batch)

    _save_sync_state(
        SYNC_STATE_PATH, {"last_date_from": date_from, "last_date_to": date_to}
    )
    print(f"Inserted {inserted} VN30F1M rows [{date_from} → {date_to}]")
    return inserted


@task(log_prints=True)
def aggregate_session_to_ohlc_5m(session_date: str | None = None) -> int:
    from core.vn30f_symbol import symbol_for_date as _sym

    if session_date is None:
        session_date = date.today().isoformat()
    contract = _sym(date.fromisoformat(session_date))
    date_from = f"{session_date} 02:00:00"
    date_to = f"{session_date} 08:00:00"
    print(f"Session {session_date}: contract={contract} → VN30F1M")
    return aggregate_ticks_to_ohlc_5m(date_from, date_to, contract)


# ---------------------------------------------------------------------------
# Prefect flow
# ---------------------------------------------------------------------------


@flow(log_prints=True)
def tick_to_ohlc_5m_pipeline(session_date: str | None = None) -> None:
    total = aggregate_session_to_ohlc_5m(session_date)
    print(f"Pipeline complete — {total} VN30F1M rows inserted.")


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Materialized-view path
#
# The MV keeps the current session live with no polling. The poll loop below no
# longer builds bars — it maintains `vn30f_front` and rewrites *finished*
# sessions authoritatively from `ticks FINAL`, which is what corrects the
# volume the MV can over-count when a tick is redelivered or the reconciler
# patches drift (see model.py for the measured example). Finished sessions
# only, so the rewrite never contends with the MV over today's partition.
# ---------------------------------------------------------------------------

FUTURES_SCOPE = "(symbol LIKE '41I1%' OR symbol LIKE 'VN30%')"


def mv_select(ticks_table: str, where_extra: str = "") -> str:
    """The one definition of a 5-minute bar, shared by the MV and the rewrite.

    Grouped by the real contract symbol: relabelling to VN30F1M happens in the
    serving view, via the `vn30f_front` join.
    """
    extra = f"\n  AND {where_extra}" if where_extra else ""
    return f"""SELECT
    symbol,
    toStartOfFiveMinutes(toTimezone(sending_time, 'Asia/Ho_Chi_Minh')) AS ts,
    argMinState(match_price, sending_time) AS open,
    max(match_price) AS high,
    min(match_price) AS low,
    argMaxState(match_price, sending_time) AS close,
    sum(match_qty) AS volume,
    sum(if(side = 1, match_qty, 0)) AS buy_volume,
    sum(if(side = 2, match_qty, 0)) AS sell_volume
FROM {ticks_table}
WHERE {FUTURES_SCOPE}{extra}
GROUP BY symbol, ts"""


def setup(cl, db: str) -> None:
    """Create the aggregate table, the front-contract map, the MV and the view."""
    from model import (
        OHLC_5M_AGG_CREATE_TABLE_DDL,
        OHLC_5M_AGG_TABLE,
        OHLC_5M_LIVE_VIEW,
        OHLC_5M_LIVE_VIEW_DDL,
        OHLC_5M_MV,
        OHLC_5M_MV_DDL,
        TICKS_CLICKHOUSE_TABLE,
        VN30F_FRONT_CREATE_TABLE_DDL,
        VN30F_FRONT_TABLE,
    )

    cl.command(OHLC_5M_AGG_CREATE_TABLE_DDL.format(database=db, table=OHLC_5M_AGG_TABLE))
    print(f"table  {db}.{OHLC_5M_AGG_TABLE}")

    cl.command(VN30F_FRONT_CREATE_TABLE_DDL.format(database=db, table=VN30F_FRONT_TABLE))
    print(f"table  {db}.{VN30F_FRONT_TABLE}")

    # No FINAL in the MV select: it sees one insert block, never the table.
    cl.command(
        OHLC_5M_MV_DDL.format(
            database=db,
            mv=OHLC_5M_MV,
            table=OHLC_5M_AGG_TABLE,
            select=mv_select(f"{db}.{TICKS_CLICKHOUSE_TABLE}"),
        )
    )
    print(f"mv     {db}.{OHLC_5M_MV}  (on {db}.{TICKS_CLICKHOUSE_TABLE})")

    cl.command(
        OHLC_5M_LIVE_VIEW_DDL.format(
            database=db,
            view=OHLC_5M_LIVE_VIEW,
            table=OHLC_5M_AGG_TABLE,
            front=VN30F_FRONT_TABLE,
        )
    )
    print(f"view   {db}.{OHLC_5M_LIVE_VIEW}")
    print(
        "\nThe MV only sees rows inserted after it existed. Run "
        "--backfill-from/--backfill-to for history; that also fills vn30f_front."
    )


def refresh_front_contracts(cl, db: str, start: date, end: date) -> int:
    """Write one `vn30f_front` row per session date in [start, end].

    The calendar rule lives in core.vn30f_symbol and nowhere else; this is how
    it reaches SQL. Weekends are included and harmless: no bars exist to join.
    """
    from core.vn30f_symbol import symbol_for_date

    from model import VN30F_FRONT_TABLE

    rows, day = [], start
    while day <= end:
        rows.append((day, symbol_for_date(day)))
        day += timedelta(days=1)

    cl.insert(f"{db}.{VN30F_FRONT_TABLE}", rows, column_names=["session_date", "symbol"])
    print(f"front  {len(rows)} session(s) {start} → {end}")
    return len(rows)


def rewrite_session(cl, db: str, session_date: date) -> int:
    """Replace one finished session's bars with the authoritative aggregate.

    DROP PARTITION then INSERT, reading `ticks FINAL` so duplicates are deduped
    before aggregation. This is the pass that makes volume exact; the MV's live
    rows for the day are discarded by the drop.
    """
    from model import OHLC_5M_AGG_TABLE, TICKS_CLICKHOUSE_TABLE

    partition = session_date.strftime("%Y%m%d")
    cl.command(f"ALTER TABLE {db}.{OHLC_5M_AGG_TABLE} DROP PARTITION {partition}")

    day = session_date.isoformat()
    window = (
        f"sending_time >= toDateTime64('{day} 02:00:00', 6, 'UTC') "
        f"AND sending_time <= toDateTime64('{day} 08:00:00', 6, 'UTC')"
    )
    cl.command(
        f"INSERT INTO {db}.{OHLC_5M_AGG_TABLE} "
        + mv_select(f"{db}.{TICKS_CLICKHOUSE_TABLE} FINAL", window)
    )
    n = cl.query(
        f"SELECT count() FROM {db}.{OHLC_5M_AGG_TABLE} WHERE toDate(ts) = '{day}'"
    ).result_rows[0][0]
    print(f"rewrote {day}: {n} bar-rows (authoritative, from ticks FINAL)")
    return n


def backfill(cl, db: str, start: date, end: date) -> None:
    """Fill the aggregate table for a past range, session by session.

    Per session rather than one big INSERT so a failure leaves a clear
    boundary, and so each DROP PARTITION stays small.
    """
    refresh_front_contracts(cl, db, start, end)
    day = start
    while day <= end:
        try:
            rewrite_session(cl, db, day)
        except Exception as exc:  # noqa: BLE001 -- one empty/odd day must not stop the run
            print(f"  {day}: skipped ({exc})")
        day += timedelta(days=1)


def maintain(cl, db: str, lookback_days: int = 2) -> None:
    """One pass of the always-on service: contract map, then finished sessions.

    Today is deliberately left to the MV — rewriting it would drop the
    partition the MV is actively writing.
    """
    today = date.today()
    refresh_front_contracts(cl, db, today, today + timedelta(days=14))
    for back in range(1, lookback_days + 1):
        day = today - timedelta(days=back)
        try:
            rewrite_session(cl, db, day)
        except Exception as exc:  # noqa: BLE001
            print(f"  {day}: rewrite skipped ({exc})")


def _aggregate_once(args) -> None:
    """One aggregation pass for the window implied by ``args``.

    Split out of ``_run_cli`` so ``--poll`` can repeat it. The session date is
    resolved per call, so a process left running overnight rolls onto the new
    day by itself.
    """
    from datetime import date as _date

    client = _get_ch_client()
    database = _get_env("CLICKHOUSE_DB", "default")
    ohlc_table = OHLC_5M_TABLE
    _ensure_ohlc_5m_table_exists(client, database, ohlc_table)

    if args.date_from:
        if not args.date_to:
            parser.error("--date-from requires --date-to")
        date_from, date_to = args.date_from, args.date_to
    else:
        session_date = args.session_date or _date.today().isoformat()
        date_from = f"{session_date} 02:00:00"
        date_to = f"{session_date} 08:00:00"

    if args.symbol:
        symbol_filter = f"AND symbol = '{args.symbol}'"
    else:
        symbol_filter = "AND (symbol LIKE '41I1%' OR symbol LIKE 'VN30%')"

    sql = f"""
        SELECT
            '{VN30F1M}',
            toStartOfFiveMinutes(toTimezone(sending_time, 'Asia/Ho_Chi_Minh')) AS ts,
            argMin(match_price, sending_time) AS open,
            max(match_price)                  AS high,
            min(match_price)                  AS low,
            argMax(match_price, sending_time) AS close,
            sum(match_qty)                    AS volume,
            sumIf(match_qty, side = 1)        AS buy_volume,
            sumIf(match_qty, side = 2)        AS sell_volume
        FROM {database}.{TICKS_TABLE} FINAL
        WHERE sending_time >= toDateTime64('{date_from}', 6, 'UTC')
          AND sending_time <= toDateTime64('{date_to}', 6, 'UTC')
          {symbol_filter}
        GROUP BY ts
        ORDER BY ts
    """

    print(f"Querying ticks [{date_from} → {date_to}] → VN30F1M ...")
    raw_rows = client.query(sql).result_rows

    if not raw_rows:
        print("No tick data found for the given window.")
        return

    rows: list[tuple] = [
        (
            VN30F1M,
            r[1] if isinstance(r[1], datetime) else datetime.fromisoformat(str(r[1])),
            float(r[2]),
            float(r[3]),
            float(r[4]),
            float(r[5]),
            int(r[6]),
            int(r[7]),
            int(r[8]),
        )
        for r in raw_rows
    ]

    column_names = [
        "symbol",
        "ts",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "buy_volume",
        "sell_volume",
    ]

    inserted = 0
    for batch in _iter_batches(rows, batch_size=50000):
        client.insert(f"{database}.{ohlc_table}", batch, column_names=column_names)
        inserted += len(batch)

    _save_sync_state(
        SYNC_STATE_PATH, {"last_date_from": date_from, "last_date_to": date_to}
    )
    print(f"Done — inserted {inserted} rows into {database}.{ohlc_table}")


def _dispatch(args) -> None:
    """Route the CLI: setup, backfill, the legacy direct write, or maintain."""
    import time
    from datetime import date as _date

    db = _get_env("CLICKHOUSE_DB", "default")

    if args.setup:
        setup(_get_ch_client(), db)
        return

    if args.backfill_from:
        start = _date.fromisoformat(args.backfill_from)
        end = (
            _date.fromisoformat(args.backfill_to) if args.backfill_to else _date.today()
        )
        backfill(_get_ch_client(), db, start, end)
        return

    # The pre-MV behaviour: aggregate straight into the old ohlc_5m table.
    if args.legacy_table:
        _aggregate_once(args)
        return

    def _pass() -> None:
        maintain(_get_ch_client(), db)

    if not args.poll:
        _pass()
        return

    print(f"Maintaining every {args.poll}s — Ctrl-C to stop.")
    while True:
        try:
            _pass()
        except Exception as exc:  # noqa: BLE001 -- a bad pass must not kill the loop
            print(f"Maintenance pass failed (retrying in {args.poll}s): {exc}")
        time.sleep(args.poll)


def _run_cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Aggregate ticks → OHLC 5m candles (plain Python, no Prefect)"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--session-date",
        metavar="YYYY-MM-DD",
        help="Aggregate a single trading session (09:00-15:00 ICT). Defaults to today.",
    )
    group.add_argument(
        "--date-from",
        metavar="YYYY-MM-DD HH:MM:SS",
        help="UTC start of custom window (pair with --date-to).",
    )
    parser.add_argument(
        "--date-to",
        metavar="YYYY-MM-DD HH:MM:SS",
        help="UTC end of custom window (pair with --date-from).",
    )
    parser.add_argument("--symbol", default=None, help="Filter to a single symbol.")
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Create ohlc_5m_agg, vn30f_front, the MV and ohlc_5m_live, then exit.",
    )
    parser.add_argument(
        "--backfill-from",
        metavar="YYYY-MM-DD",
        help="Backfill the aggregate table from this session (pair with --backfill-to).",
    )
    parser.add_argument(
        "--backfill-to",
        metavar="YYYY-MM-DD",
        help="Last session to backfill (defaults to today).",
    )
    parser.add_argument(
        "--legacy-table",
        action="store_true",
        help=(
            "Write bars directly into the old ohlc_5m table instead of "
            "maintaining the MV path. Retained for a rollback."
        ),
    )
    parser.add_argument(
        "--poll",
        type=int,
        default=None,
        metavar="SECONDS",
        help=(
            "Re-aggregate every SECONDS instead of exiting. Used by the "
            "worker-ohlc-5m service to keep the current session fresh for the "
            "Future page, which reads ohlc_5m intraday. Each pass rewrites the "
            "whole session and ReplacingMergeTree dedupes, so repeating is safe."
        ),
    )
    parser.add_argument(
        "deploy",
        nargs="?",
        help="Pass 'deploy' as positional arg to register Prefect deployments instead.",
    )
    args = parser.parse_args()

    if args.deploy == "deploy":
        from pathlib import Path

        tick_to_ohlc_5m_pipeline.from_source(
            source=str(Path(__file__).parent.parent),
            entrypoint="workers/ohlc_5m.py:tick_to_ohlc_5m_pipeline",
        ).deploy(
            name="vn30f1m-ohlc-5m",
            work_pool_name="my-worker",
            cron="5 8 * * 1-5",  # 08:05 UTC = 15:05 ICT, weekdays
        )
        return

    _dispatch(args)


if __name__ == "__main__":
    _run_cli()
