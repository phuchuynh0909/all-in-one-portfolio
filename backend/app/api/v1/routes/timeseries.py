from fastapi import APIRouter, Depends
from fastapi_cache.decorator import cache
from sqlalchemy.orm import Session
from app.schemas.timeseries import (
    TimeseriesResponse,
    TimeseriesRequest,
    BarsRequest,
    BarsResponse,
    IndicatorsOnlyResponse,
    IndicatorsRequest,
    MarketBreadthResponse,
    MarketBreadthRequest,
)
from app.schemas.sector import (
    SectorConstituents,
    SectorConstituentsRequest,
    SectorDominance,
    SectorDominanceRequest,
    SectorRelativeStrength,
    SectorRelativeStrengthRequest,
    SectorRotation,
    SectorRotationRequest,
    SectorTimeseries,
)
from app.services.stock_service import (
    get_stock_timeseries,
    get_stock_bars,
    get_sector_timeseries,
    get_sector_relative_strength,
    get_sector_dominance,
    get_sector_constituents,
    get_sector_rotation,
    get_stock_indicators,
    get_market_indicators,
)
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


@router.post("/{symbol}/bars", response_model=BarsResponse)
@cache(expire=300)  # Cache for 5 minutes
async def get_symbol_bars(
    symbol: str,
    request: BarsRequest
) -> BarsResponse:
    """
    Get one page of bars (with indicators) for a symbol.

    Paginates server-side for the TradingView datafeed: returns `count_back`
    bars ending just before `to`, plus `has_more_history` / `next_time` so the
    chart knows whether to keep scrolling back.
    """
    return await get_stock_bars(
        symbol=symbol,
        interval=request.interval,
        indicators=request.indicators,
        to_ts=request.to,
        count_back=request.count_back,
        from_ts=request.from_ts,
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


# Not @cache'd: fastapi-cache keys on the arguments, and the `db` session's repr
# carries an object id, so every request would miss and grow the cache instead.
@router.post("/sector/{sector_level}/relative-strength", response_model=SectorRelativeStrength)
async def sector_relative_strength(
    sector_level: str,
    request: SectorRelativeStrengthRequest = SectorRelativeStrengthRequest(),
    db: Session = Depends(get_db),
) -> SectorRelativeStrength:
    """
    Relative strength of every sector at `sector_level` against VNINDEX.

    Returns the last `lookback` sessions as a dense matrix (rows strongest-first
    at T-0) — the shape the sector heatmap draws.
    """
    return await get_sector_relative_strength(
        sector_level=sector_level,
        lookback=request.lookback,
        window=request.window,
        metric=request.metric,
        timeframe=request.timeframe,
        db=db,
    )


@router.post("/sector/{sector_level}/dominance", response_model=SectorDominance)
async def sector_dominance(
    sector_level: str,
    request: SectorDominanceRequest = SectorDominanceRequest(),
    db: Session = Depends(get_db),
) -> SectorDominance:
    """
    Rank sectors at `sector_level` by sustained leadership rather than latest RS.

    Blends persistence, constituent breadth, RS momentum and turnover share, each
    rank-scaled to 0-100. Every component is returned so the table can be sorted
    on any of them.
    """
    return await get_sector_dominance(
        sector_level=sector_level,
        lookback=request.lookback,
        window=request.window,
        metric=request.metric,
        timeframe=request.timeframe,
        min_constituents=request.min_constituents,
        db=db,
    )


@router.post("/sector/{sector_level}/rotation", response_model=SectorRotation)
async def sector_rotation(
    sector_level: str,
    request: SectorRotationRequest = SectorRotationRequest(),
    db: Session = Depends(get_db),
) -> SectorRotation:
    """
    Relative rotation graph coordinates for `sector_level`: RS-ratio vs RS-momentum.

    Both axes centre on 100 (the benchmark). Each sector carries a `tail` of bars
    so the direction of travel through the quadrants is visible.
    """
    return await get_sector_rotation(
        sector_level=sector_level,
        tail=request.tail,
        window=request.window,
        momentum_window=request.momentum_window,
        timeframe=request.timeframe,
        db=db,
    )


@router.post("/sector/{sector_level}/{sector_id}/constituents", response_model=SectorConstituents)
async def sector_constituents(
    sector_level: str,
    sector_id: int,
    request: SectorConstituentsRequest = SectorConstituentsRequest(),
    db: Session = Depends(get_db),
) -> SectorConstituents:
    """
    One sector's constituents, each with its own relative strength.

    Same measure, window and benchmark as the sector panels, plus a 1-99
    percentile against every constituent at this level.
    """
    return await get_sector_constituents(
        sector_level=sector_level,
        sector_id=sector_id,
        window=request.window,
        metric=request.metric,
        timeframe=request.timeframe,
        db=db,
    )


@router.post("/market/breadth", response_model=MarketBreadthResponse)
@cache(expire=300)  # Cache for 5 minutes
async def market_breadth(
    request: MarketBreadthRequest
) -> MarketBreadthResponse:
    """
    Get market breadth indicators:
    - A/D Line (Advance-Decline Line): Cumulative breadth
    - McClellan Oscillator: Short-term breadth momentum (19 EMA - 39 EMA)
    - McClellan Summation Index: Long-term breadth momentum
    - Daily advances/declines/unchanged counts
    """
    return await get_market_indicators(
        start_date=request.start_date,
        end_date=request.end_date
    )