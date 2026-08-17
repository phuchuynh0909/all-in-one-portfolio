"""Block-episode ("large-execution footprint") API endpoints."""
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from clickhouse_connect.driver import Client

from app.db.clickhouse import get_clickhouse_client
from app.services.block_episodes_service import (
    BlockEpisodesService,
    BlockEpisodesResponse,
)

router = APIRouter()


def get_block_episodes_service(
    clickhouse_client: Client = Depends(get_clickhouse_client),
) -> BlockEpisodesService:
    return BlockEpisodesService(clickhouse_client)


@router.get("/block-episodes", response_model=BlockEpisodesResponse, tags=["Block Episodes"])
async def get_block_episodes(
    symbol: str = Query(..., description="Symbol, e.g. FPT"),
    from_date: Optional[str] = Query(
        None, description="Start date YYYY-MM-DD (default: 30 days ago)"
    ),
    to_date: Optional[str] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    side: Optional[int] = Query(
        None, ge=1, le=2, description="Filter by aggressor side: 1=BUY, 2=SELL"
    ),
    candidate_type: Optional[str] = Query(
        None,
        description="Filter by type: FLOW_CLUSTER, LARGE_PRINT, or FLOW_CLUSTER_AND_LARGE_PRINT",
    ),
    min_abs_notional: Optional[float] = Query(
        None, ge=0, description="Only episodes with gross notional >= this value"
    ),
    limit: int = Query(1000, ge=1, le=10000, description="Max episodes returned"),
    service: BlockEpisodesService = Depends(get_block_episodes_service),
):
    """Intraday large-execution footprints for a symbol over a date range.

    Each episode is a stitched run of same-direction candidate 1-second bins
    (a flow cluster, a large print, or both). A footprint is evidence of
    sustained one-sided execution — not confirmation of an institution or a
    parent-order owner.
    """
    to_day = date.fromisoformat(to_date) if to_date else date.today()
    from_day = (
        date.fromisoformat(from_date) if from_date else to_day - timedelta(days=30)
    )
    try:
        return service.get_episodes(
            symbol=symbol.strip().upper(),
            from_day=from_day,
            to_day=to_day,
            side=side,
            candidate_type=candidate_type,
            min_abs_notional=min_abs_notional,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
