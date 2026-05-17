from typing import Dict, List, Optional, Union, Any
from datetime import date, datetime
from pydantic import BaseModel, Field

class Trade(BaseModel):
    symbol: str
    date: datetime
    entry_price: float
    return_pct: float
    type: str = Field(description="Either 'open_trades' or 'closed_trades'")
    entry_idx: int
    exit_idx: Optional[int] = None
    close_date: Optional[datetime] = None
    trading_days: Optional[int] = None
    metadata: Optional[Dict] = None
    y_pred_xgb: Optional[float] = None
    y_pred_lgbm: Optional[float] = None
    y_pred_catboost: Optional[float] = None
    y_pred_ensemble: Optional[float] = None
    msr_rank_10: Optional[float] = None
    risk_regime: Optional[bool] = None
    market_risk_regime: Optional[bool] = None
    breadth_regime: Optional[bool] = None

class BacktestRequest(BaseModel):
    strategy: str = Field(description="Strategy name to use for backtesting")
    start_date: str = Field(description="Start date in YYYY-MM-DD format")
    symbols: Optional[List[str]] = None
    apply_ml: bool = Field(default=True, description="Whether to apply ML predictions to trades")

class ExecutionTime(BaseModel):
    total_seconds: float
    data_loading_seconds: float
    strategy_seconds: float
    feature_building_seconds: float
    prediction_seconds: float

class BacktestResponse(BaseModel):
    open_trades: List[Trade]
    closed_trades: List[Trade]
    execution_time: ExecutionTime


class BacktestPlotResponse(BaseModel):
    symbol: str
    start_date: str
    strategy: str
    html: str
    stats: Optional[Dict[str, Any]] = None


# ============================================================================
# H5 Backtest Results Schemas (for pre-computed backtest from notebook)
# ============================================================================

class H5Trade(BaseModel):
    """Trade record from H5 backtest results"""
    id: int
    symbol: str
    size: float
    entry_timestamp: datetime
    avg_entry_price: float
    entry_fees: float
    exit_timestamp: datetime
    avg_exit_price: float
    exit_fees: float
    pnl: float
    return_pct: float  # renamed from 'return' which is a Python keyword
    direction: str
    status: str


class H5Stats(BaseModel):
    """Stats for a symbol from H5 backtest results"""
    symbol: str
    start: Optional[str] = None
    end: Optional[str] = None
    period: Optional[str] = None
    start_value: Optional[float] = None
    end_value: Optional[float] = None
    total_return_pct: Optional[float] = None
    benchmark_return_pct: Optional[float] = None
    max_gross_exposure_pct: Optional[float] = None
    total_fees_paid: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    max_drawdown_duration: Optional[str] = None
    total_trades: Optional[int] = None
    total_closed_trades: Optional[int] = None
    total_open_trades: Optional[int] = None
    open_trade_pnl: Optional[float] = None
    win_rate_pct: Optional[float] = None
    best_trade_pct: Optional[float] = None
    worst_trade_pct: Optional[float] = None
    avg_winning_trade_pct: Optional[float] = None
    avg_losing_trade_pct: Optional[float] = None
    avg_winning_trade_duration: Optional[str] = None
    avg_losing_trade_duration: Optional[str] = None
    sharpe_ratio: Optional[float] = None
    sortino_ratio: Optional[float] = None
    calmar_ratio: Optional[float] = None
    omega_ratio: Optional[float] = None
    profit_factor: Optional[float] = None
    expectancy: Optional[float] = None


class H5BacktestResultsResponse(BaseModel):
    """Response containing backtest results from H5 file for a single symbol"""
    symbol: str
    trades: List[H5Trade]
    stats: Optional[H5Stats] = None
    total_trades: int
