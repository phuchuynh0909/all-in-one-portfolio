"""Prefect flow: aggregate tick data into 1-hour OHLC candles."""

from typing import Any, Generator, Sequence
from datetime import date, datetime
from prefect import flow, task

import os
import json
import math
import clickhouse_connect  # type: ignore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SYNC_STATE_PATH = os.getenv(
    "OHLC_1H_SYNC_STATE_PATH", "./.state/ohlc_1h_sync_state.json"
)
TICKS_TABLE = os.getenv("CLICKHOUSE_TICKS_TABLE", "ticks")
OHLC_1H_TABLE = os.getenv("CLICKHOUSE_OHLC_1H_TABLE", "ohlc_1h")

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _get_env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _get_ch_client():
    host = _get_env("CLICKHOUSE_HOST", "localhost")
    port = int(_get_env("CLICKHOUSE_PORT", "9010"))
    username = _get_env("CLICKHOUSE_USER", "myuser")
    password = _get_env("CLICKHOUSE_PASSWORD", "mypassword")
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


def _ensure_ohlc_1h_table_exists(client, database: str, table: str) -> None:
    client.command(f"CREATE DATABASE IF NOT EXISTS {database}")
    client.command(
        f"""
        CREATE TABLE IF NOT EXISTS {database}.{table} (
            symbol String,
            ts DateTime('UTC'),
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


@task(log_prints=True)
def aggregate_ticks_to_ohlc_1h(
    date_from: str,
    date_to: str,
    symbol: str | None = None,
) -> int:
    """Aggregate raw ticks into 1-hour OHLC candles.

    Args:
        date_from: UTC datetime string, e.g. "2026-03-26 02:00:00"
        date_to:   UTC datetime string, e.g. "2026-03-26 08:00:00"
        symbol:    Optional symbol filter; None = all symbols.

    Returns:
        Number of inserted rows.
    """
    database = _get_env("CLICKHOUSE_DB", "default")
    ticks_table = TICKS_TABLE
    ohlc_table = OHLC_1H_TABLE

    client = _get_ch_client()
    _ensure_ohlc_1h_table_exists(client, database, ohlc_table)

    symbol_filter = f"AND symbol = '{symbol}'" if symbol else ""

    sql = f"""
        SELECT
            symbol,
            toStartOfHour(sending_time) AS ts,
            argMin(match_price, sending_time) AS open,
            max(match_price) AS high,
            min(match_price) AS low,
            argMax(match_price, sending_time) AS close,
            sum(match_qty) AS volume,
            sumIf(match_qty, side = 1) AS buy_volume,
            sumIf(match_qty, side = 2) AS sell_volume
        FROM {database}.{ticks_table} FINAL
        WHERE sending_time >= toDateTime64('{date_from}', 6, 'UTC')
          AND sending_time <= toDateTime64('{date_to}', 6, 'UTC')
          {symbol_filter}
        GROUP BY symbol, ts
        ORDER BY symbol, ts
    """

    result = client.query(sql)
    raw_rows = result.result_rows

    rows: list[tuple] = [
        (
            str(r[0]),
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
        client.insert(
            f"{database}.{ohlc_table}",
            batch,
            column_names=column_names,
        )
        inserted += len(batch)

    _save_sync_state(
        SYNC_STATE_PATH,
        {"last_date_from": date_from, "last_date_to": date_to},
    )

    print(
        f"Inserted {inserted} OHLC-1h rows into {database}.{ohlc_table} "
        f"[{date_from} → {date_to}]"
    )
    return inserted


@task(log_prints=True)
def aggregate_session_to_ohlc_1h(
    session_date: str | None = None,
    symbol: str | None = None,
) -> int:
    """Convenience wrapper: aggregate a single trading session (09:00-15:00 ICT).

    Args:
        session_date: ISO date string (YYYY-MM-DD). Defaults to today.
        symbol:       Optional symbol filter.

    Returns:
        Number of inserted rows.
    """
    if session_date is None:
        session_date = date.today().isoformat()

    # Session window: 09:00-15:00 ICT = 02:00-08:00 UTC
    date_from = f"{session_date} 02:00:00"
    date_to = f"{session_date} 08:00:00"

    print(f"Aggregating session {session_date} (UTC {date_from} → {date_to})")
    return aggregate_ticks_to_ohlc_1h(date_from, date_to, symbol)


# ---------------------------------------------------------------------------
# Prefect flow
# ---------------------------------------------------------------------------


@flow(log_prints=True)
def tick_to_ohlc_1h_pipeline(
    session_date: str | None = None,
    symbol: str | None = None,
) -> None:
    """Orchestrate tick → 1-hour OHLC aggregation for a trading session."""
    total = aggregate_session_to_ohlc_1h(session_date, symbol)
    print(f"Pipeline complete — {total} total OHLC-1h rows inserted.")


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------


def _run_cli() -> None:
    import argparse
    from datetime import date as _date

    parser = argparse.ArgumentParser(
        description="Aggregate ticks → OHLC 1h candles (plain Python, no Prefect)"
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
        "deploy",
        nargs="?",
        help="Pass 'deploy' as positional arg to register Prefect deployments instead.",
    )
    args = parser.parse_args()

    if args.deploy == "deploy":
        from pathlib import Path

        tick_to_ohlc_1h_pipeline.from_source(
            source=str(Path(__file__).parent),
            entrypoint="ohlc_1h.py:tick_to_ohlc_1h_pipeline",
        ).deploy(
            name="tick-to-ohlc-1h",
            work_pool_name="my-worker",
            cron="5 8 * * 1-5",  # 08:05 UTC = 15:05 ICT, weekdays
        )
        return

    client = _get_ch_client()
    database = _get_env("CLICKHOUSE_DB", "default")
    ohlc_table = OHLC_1H_TABLE
    _ensure_ohlc_1h_table_exists(client, database, ohlc_table)

    if args.date_from:
        if not args.date_to:
            parser.error("--date-from requires --date-to")
        date_from, date_to = args.date_from, args.date_to
    else:
        session_date = args.session_date or _date.today().isoformat()
        date_from = f"{session_date} 02:00:00"
        date_to = f"{session_date} 08:00:00"

    symbol_filter = f"AND symbol = '{args.symbol}'" if args.symbol else ""

    sql = f"""
        SELECT
            symbol,
            toStartOfHour(sending_time) AS ts,
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
        GROUP BY symbol, ts
        ORDER BY symbol, ts
    """

    print(f"Querying ticks [{date_from} → {date_to}] ...")
    raw_rows = client.query(sql).result_rows

    if not raw_rows:
        print("No tick data found for the given window.")
        return

    rows: list[tuple] = [
        (
            str(r[0]),
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


if __name__ == "__main__":
    _run_cli()
