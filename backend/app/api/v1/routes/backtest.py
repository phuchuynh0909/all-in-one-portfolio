from fastapi import APIRouter, Request
from app.schemas.backtest import BacktestRequest, BacktestResponse
from app.services.backtest_service import run_backtest
from fastapi_cache.decorator import cache
from loguru import logger
import hashlib
import json

router = APIRouter(prefix="/backtest", tags=["backtest"])

@router.post("", response_model=BacktestResponse)
@cache(expire=3600)
async def backtest_strategy(request: BacktestRequest) -> BacktestResponse:
    """
    Run backtest for a given strategy.
    """
    # Log request details for debugging
    logger.debug(f"Received backtest request: strategy={request.strategy}, start_date={request.start_date}")
    
    """
    Run backtest for a given strategy.
    
    Available strategies:
    - "Squeeze Breakout"
    - "Breakout TTM Version 2"
    
    Each strategy will be run with multiple parameter sets and ML models will be used
    to predict trade outcomes.
    """
    result = await run_backtest(
        strategy_name=request.strategy,
        start_date=request.start_date,
        symbols=request.symbols
    )
    
    return BacktestResponse(**result)
