from typing import Dict, List, Optional, Union
from datetime import date, datetime
from pydantic import BaseModel, Field

class Trade(BaseModel):
    symbol: str
    date: datetime
    entry_price: float
    pnl: float
    type: str = Field(description="Either 'open_trades' or 'closed_trades'")
    entry_idx: int
    exit_idx: Optional[int] = None
    close_date: Optional[datetime] = None
    trading_days: Optional[int] = None
    metadata: Optional[Dict] = None
    y_pred_xgb: Optional[float] = None
    y_pred_lgbm: Optional[float] = None
    y_pred_catboost: Optional[float] = None
    msr_rank_10: Optional[float] = None

class BacktestRequest(BaseModel):
    strategy: str = Field(description="Strategy name to use for backtesting")
    start_date: str = Field(description="Start date in YYYY-MM-DD format")
    symbols: Optional[List[str]] = None

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
