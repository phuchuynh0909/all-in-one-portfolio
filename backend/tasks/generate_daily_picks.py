"""Generate and persist the daily stock-pick snapshot.

Run from ``backend/`` after the EOD OHLC sync has completed::

    python -m tasks.generate_daily_picks
    python -m tasks.generate_daily_picks --as-of 2026-10-02 --limit 10
    python -m tasks.generate_daily_picks --deploy

The generator resolves the latest sufficiently complete session when ``--as-of``
is omitted. It never places orders; it only writes an auditable snapshot.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import clickhouse_connect
from loguru import logger
from prefect import flow

from app.core.settings import settings
from app.services.daily_picks_service import DailyPicksService
from app.schemas.daily_picks import DailyPickSnapshot

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def generate_daily_picks(
    *, as_of: date | None = None, limit: int = 10
) -> DailyPickSnapshot:
    client = clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_db,
    )
    try:
        snapshot = DailyPicksService(client).generate_and_save(
            as_of=as_of,
            limit=limit,
        )
        logger.info(
            "Saved daily picks: as_of={}, candidates={}, picks={}, model={}",
            snapshot.as_of,
            snapshot.candidate_size,
            len(snapshot.picks),
            snapshot.model_version,
        )
        return snapshot
    finally:
        client.close()


@flow(name="generate-daily-picks", log_prints=True)
def generate_daily_picks_flow(
    as_of: str | None = None,
    limit: int = 10,
) -> dict:
    """Prefect entry point scheduled after EOD sync and model refresh."""
    parsed_date = date.fromisoformat(as_of) if as_of else None
    snapshot = generate_daily_picks(as_of=parsed_date, limit=limit)
    return snapshot.model_dump(mode="json")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the daily stock-pick snapshot")
    parser.add_argument("--as-of", type=date.fromisoformat, help="Completed session YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=10, help="Maximum picks (1-50)")
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="Register the weekday Prefect deployment instead of generating now",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    if args.deploy:
        generate_daily_picks_flow.from_source(
            source=str(PROJECT_ROOT),
            entrypoint="backend/tasks/generate_daily_picks.py:generate_daily_picks_flow",
        ).deploy(  # pyright: ignore[reportAttributeAccessIssue]
            name="generate-daily-picks",
            work_pool_name="my-worker",
            # 17:00 ICT, after the EOD sync and scheduled meta-model refresh.
            cron="0 10 * * 1-5",
        )
        return 0
    generate_daily_picks_flow(
        as_of=args.as_of.isoformat() if args.as_of else None,
        limit=args.limit,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
