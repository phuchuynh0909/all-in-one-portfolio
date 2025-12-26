from fastapi import APIRouter, Request, HTTPException, Query
from app.schemas.backtest import (
    BacktestRequest, 
    BacktestResponse, 
    H5BacktestResultsResponse, 
    H5Trade, 
    H5Stats
)
from app.services.backtest_service import run_backtest
from fastapi_cache.decorator import cache
from fastapi_cache import FastAPICache
from loguru import logger
from typing import Optional, List
import hashlib
import json
import pandas as pd
import numpy as np
from pathlib import Path

router = APIRouter(prefix="/backtest", tags=["backtest"])


def backtest_key_builder(
    func,
    namespace: str = "",
    *,
    request: Request = None,
    response = None,
    args = None,
    kwargs = None,
):
    """
    Custom cache key builder that includes POST body in cache key.
    This ensures different request bodies generate different cache keys.
    """
    # Get the BacktestRequest from kwargs
    backtest_request = kwargs.get("request") if kwargs else None
    
    if backtest_request:
        # Create a unique key from the request parameters
        key_data = {
            "strategy": backtest_request.strategy,
            "start_date": backtest_request.start_date,
            "symbols": sorted(backtest_request.symbols) if backtest_request.symbols else None,
        }
        body_hash = hashlib.md5(json.dumps(key_data, sort_keys=True).encode()).hexdigest()
    else:
        body_hash = "no_body"
    
    prefix = FastAPICache.get_prefix()
    cache_key = f"{prefix}:backtest:{func.__name__}:{body_hash}"
    
    logger.debug(f"Cache key generated: {cache_key}")
    return cache_key

# Path to H5 backtest results file (backend is mounted at /app in Docker)
H5_BACKTEST_FILE = Path("/app/assets/backtest_results.h5")


def _safe_value(val):
    """Convert numpy/pandas types to Python native types, handle NaN/None/NaT"""
    if val is None:
        return None
    if pd.isna(val):  # Handles NaN, NaT, None
        return None
    if isinstance(val, (np.integer, np.floating)):
        return float(val) if isinstance(val, np.floating) else int(val)
    if isinstance(val, pd.Timestamp):
        return val.isoformat()
    if isinstance(val, pd.Timedelta):
        return str(val)
    return val


@router.get("/watchlist", response_model=List[str])
async def get_watchlist() -> List[str]:
    """Get list of symbols from watchlist.csv"""
    watchlist_path = Path("/app/models/watchlist.csv")
    if not watchlist_path.exists():
        raise HTTPException(status_code=404, detail=f"Watchlist file not found: {watchlist_path}")
    
    try:
        with open(watchlist_path, 'r') as f:
            symbols = [line.strip() for line in f if line.strip()]
        return sorted(symbols)
    except Exception as e:
        logger.error(f"Error reading watchlist: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/h5/results", response_model=H5BacktestResultsResponse)
async def get_h5_backtest_results(
    symbol: str = Query("VNINDEX", description="Filter by symbol"),
) -> H5BacktestResultsResponse:
    """
    Get backtest results from pre-computed H5 file.
    Optionally filter by symbol.
    """
    if not H5_BACKTEST_FILE.exists():
        raise HTTPException(status_code=404, detail=f"Backtest results file not found: {H5_BACKTEST_FILE}")
    
    try:
        # Read trades
        trades_df = pd.read_hdf(H5_BACKTEST_FILE, key='trades')
        # Read stats
        stats_df = pd.read_hdf(H5_BACKTEST_FILE, key='stats')
        
        # Filter trades and stats DataFrames by symbol (from code block 994-999 / 1003-1008)
        trades_df = trades_df[trades_df['symbol'] == symbol]
        # Filter stats_df by symbol in the index
        if symbol not in stats_df.index:
            raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not found in backtest stats.")
        stats_df = stats_df.loc[[symbol]]
        # Convert trades to response format
        trades = []

        for _, row in trades_df.iterrows():
            trade = H5Trade(
                id=int(row.get('Exit Trade Id')),
                symbol=symbol,
                size=_safe_value(row.get('Size', 0)) or 0,
                entry_timestamp=pd.to_datetime(str(row.get('Entry Timestamp', ''))),
                avg_entry_price=_safe_value(row.get('Avg Entry Price', 0)) or 0,
                entry_fees=_safe_value(row.get('Entry Fees', 0)) or 0,
                exit_timestamp=pd.to_datetime(str(row.get('Exit Timestamp', ''))),
                avg_exit_price=_safe_value(row.get('Avg Exit Price', 0)) or 0,
                exit_fees=_safe_value(row.get('Exit Fees', 0)) or 0,
                pnl=_safe_value(row.get('PnL', 0)) or 0,
                return_pct=(_safe_value(row.get('Return', 0)) or 0) * 100,
                direction=str(row.get('Direction', 'Long')),
                status=str(row.get('Status', 'Closed')),
            )
            trades.append(trade)
        
        # Convert stats to response format (single object for the selected symbol)
        stats_row = stats_df.iloc[0] if not stats_df.empty else None
        stats = None
        if stats_row is not None:
            stats = H5Stats(
                symbol=symbol,
                start=_safe_value(stats_row.get('Start')),
                end=_safe_value(stats_row.get('End')),
                period=_safe_value(stats_row.get('Period')),
                start_value=_safe_value(stats_row.get('Start Value')),
                end_value=_safe_value(stats_row.get('End Value')),
                total_return_pct=_safe_value(stats_row.get('Total Return [%]')),
                benchmark_return_pct=_safe_value(stats_row.get('Benchmark Return [%]')),
                max_gross_exposure_pct=_safe_value(stats_row.get('Max Gross Exposure [%]')),
                total_fees_paid=_safe_value(stats_row.get('Total Fees Paid')),
                max_drawdown_pct=_safe_value(stats_row.get('Max Drawdown [%]')),
                max_drawdown_duration=_safe_value(stats_row.get('Max Drawdown Duration')),
                total_trades=int(stats_row.get('Total Trades', 0)) if not pd.isna(stats_row.get('Total Trades')) else 0,
                total_closed_trades=int(stats_row.get('Total Closed Trades', 0)) if not pd.isna(stats_row.get('Total Closed Trades')) else 0,
                total_open_trades=int(stats_row.get('Total Open Trades', 0)) if not pd.isna(stats_row.get('Total Open Trades')) else 0,
                open_trade_pnl=_safe_value(stats_row.get('Open Trade PnL')),
                win_rate_pct=_safe_value(stats_row.get('Win Rate [%]')),
                best_trade_pct=_safe_value(stats_row.get('Best Trade [%]')),
                worst_trade_pct=_safe_value(stats_row.get('Worst Trade [%]')),
                avg_winning_trade_pct=_safe_value(stats_row.get('Avg Winning Trade [%]')),
                avg_losing_trade_pct=_safe_value(stats_row.get('Avg Losing Trade [%]')),
                avg_winning_trade_duration=_safe_value(stats_row.get('Avg Winning Trade Duration')),
                avg_losing_trade_duration=_safe_value(stats_row.get('Avg Losing Trade Duration')),
                sharpe_ratio=_safe_value(stats_row.get('Sharpe Ratio')),
                sortino_ratio=_safe_value(stats_row.get('Sortino Ratio')),
                calmar_ratio=_safe_value(stats_row.get('Calmar Ratio')),
                omega_ratio=_safe_value(stats_row.get('Omega Ratio')),
                profit_factor=(
                    None
                    if _safe_value(stats_row.get('Profit Factor')) in [float('inf'), float('-inf')]
                    else _safe_value(stats_row.get('Profit Factor'))
                ),
                expectancy=_safe_value(stats_row.get('Expectancy')),
            )
        
        return H5BacktestResultsResponse(
            symbol=symbol,
            trades=trades,
            stats=stats,
            total_trades=len(trades),
        )
        
    except Exception as e:
        logger.error(f"Error reading H5 backtest results: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", response_model=BacktestResponse)
@cache(expire=3600, key_builder=backtest_key_builder)
async def backtest_strategy(request: BacktestRequest) -> BacktestResponse:
    """
    Run backtest for a given strategy.
    
    Available strategies:
    - "Squeeze Breakout"
    - "Breakout TTM Version 2"
    - "Dual RSI"
    
    Each strategy will be run with multiple parameter sets and ML models will be used
    to predict trade outcomes.
    """
    logger.debug(f"Received backtest request: strategy={request.strategy}, start_date={request.start_date}")
    
    result = await run_backtest(
        strategy_name=request.strategy,
        start_date=request.start_date,
        symbols=request.symbols
    )
    
    return BacktestResponse(**result)
