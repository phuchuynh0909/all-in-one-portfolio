"""Large-order (Layer 3 block) API endpoints."""
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from clickhouse_connect.driver import Client

from app.db.clickhouse import get_clickhouse_client
from app.services.large_orders_service import (
    LargeOrdersService,
    LargeOrdersResponse,
)

router = APIRouter()


def get_large_orders_service(
    clickhouse_client: Client = Depends(get_clickhouse_client),
) -> LargeOrdersService:
    return LargeOrdersService(clickhouse_client)


@router.get("/large-orders", response_model=LargeOrdersResponse)
async def get_large_orders(
    symbol: str = Query(..., description="Symbol, e.g. FPT"),
    from_date: Optional[str] = Query(
        None, description="Start date YYYY-MM-DD (default: 400 days ago)"
    ),
    to_date: Optional[str] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    min_value: Optional[float] = Query(
        None, ge=0, description="Only blocks with notional >= this value"
    ),
    service: LargeOrdersService = Depends(get_large_orders_service),
):
    """One net large-order bubble per trading day for a symbol over a range.

    Per day: net delta = buy notional - sell notional (sign = bubble side),
    `total_value` drives the size tier, `total_qty` is the volume label, and
    `time` aligns to the day's 1D candle.
    """
    to_day = date.fromisoformat(to_date) if to_date else date.today()
    from_day = date.fromisoformat(from_date) if from_date else to_day - timedelta(days=400)
    return service.get_blocks(
        symbol=symbol.strip().upper(),
        from_day=from_day,
        to_day=to_day,
        min_value=min_value,
    )
