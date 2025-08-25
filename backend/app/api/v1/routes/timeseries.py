from fastapi import APIRouter
from fastapi_cache.decorator import cache
from app.schemas.timeseries import TimeseriesResponse, TimeseriesRequest
from app.schemas.sector import SectorTimeseries
from app.services.stock_service import get_stock_timeseries, get_sector_timeseries

router = APIRouter(prefix="/timeseries", tags=["timeseries"])

@router.post("/{symbol}", response_model=TimeseriesResponse)
@cache(expire=300)  # Cache for 5 minutes
async def get_symbol_timeseries(
    symbol: str,
    request: TimeseriesRequest
) -> TimeseriesResponse:
    """
    Get timeseries data for a symbol with optional technical indicators.
    """
    return await get_stock_timeseries(
        symbol=symbol,
        interval=request.interval,
        indicators=request.indicators,
        start_date=request.start_date,
        end_date=request.end_date
    )

@router.post("/sector/{sector_level}", response_model=SectorTimeseries)
async def sector_timeseries(
    sector_level: str
) -> TimeseriesResponse:
    """
    Get timeseries data for a sector with optional technical indicators.
    """
    return await get_sector_timeseries(
        sector_level=sector_level
    )