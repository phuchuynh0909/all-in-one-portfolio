from fastapi import APIRouter, Depends
from fastapi_cache.decorator import cache
from sqlalchemy.orm import Session
from app.schemas.timeseries import TimeseriesResponse, TimeseriesRequest, IndicatorsOnlyResponse, IndicatorsRequest
from app.schemas.sector import SectorTimeseries
from app.services.stock_service import get_stock_timeseries, get_sector_timeseries, get_stock_indicators
from app.db.base import get_db

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


@router.post("/{symbol}/indicators", response_model=IndicatorsOnlyResponse)
@cache(expire=300)  # Cache for 5 minutes
async def get_symbol_indicators(
    symbol: str,
    request: IndicatorsRequest
) -> IndicatorsOnlyResponse:
    """
    Get indicators only for a symbol (without OHLCV data).
    Lighter response for when only indicator values are needed.
    """
    return await get_stock_indicators(
        symbol=symbol,
        indicators=request.indicators,
        start_date=request.start_date,
        end_date=request.end_date
    )

@router.post("/sector/{sector_level}", response_model=SectorTimeseries)
async def sector_timeseries(
    sector_level: str,
    db: Session = Depends(get_db)
) -> SectorTimeseries:
    """
    Get timeseries data for a sector with optional technical indicators.
    """
    return await get_sector_timeseries(
        sector_level=sector_level,
        db=db
    )