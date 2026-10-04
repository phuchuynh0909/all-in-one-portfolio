"""Read generated daily stock-pick snapshots."""
from __future__ import annotations

from datetime import date

from clickhouse_connect.driver.client import Client
from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.clickhouse import get_clickhouse_client
from app.schemas.daily_picks import DailyPickHistory, DailyPickSnapshot
from app.services.daily_picks_service import DailyPicksService

router = APIRouter(prefix="/daily-picks", tags=["daily picks"])


def get_daily_picks_service(
    clickhouse_client: Client = Depends(get_clickhouse_client),
) -> DailyPicksService:
    return DailyPicksService(clickhouse_client)


@router.get("/latest", response_model=DailyPickSnapshot)
def latest_daily_picks(
    service: DailyPicksService = Depends(get_daily_picks_service),
) -> DailyPickSnapshot:
    snapshot = service.get_snapshot()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No daily-pick snapshot has been generated")
    return snapshot


@router.get("/history", response_model=DailyPickHistory)
def daily_picks_history(
    limit: int = Query(default=60, ge=1, le=365),
    service: DailyPicksService = Depends(get_daily_picks_service),
) -> DailyPickHistory:
    return service.get_history(limit=limit)


@router.get("/{as_of}", response_model=DailyPickSnapshot)
def daily_picks_for_date(
    as_of: date,
    service: DailyPicksService = Depends(get_daily_picks_service),
) -> DailyPickSnapshot:
    snapshot = service.get_snapshot(as_of)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=f"No daily-pick snapshot for {as_of.isoformat()}",
        )
    return snapshot
