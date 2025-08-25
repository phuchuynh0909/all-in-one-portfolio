from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, Field
from enum import Enum


class PositionBase(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10)
    quantity: Decimal = Field(..., gt=0)
    purchase_price: Decimal = Field(..., gt=0)
    current_price: Optional[Decimal] = None
    purchase_date: date
    notes: Optional[str] = None


class PositionCreate(PositionBase):
    pass


class Position(PositionBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class TransactionBase(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=10)
    transaction_type: str = Field(..., pattern="^(buy|sell)$")
    quantity: Decimal = Field(..., gt=0)
    price: Decimal = Field(..., gt=0)
    close_price: Optional[Decimal] = None
    transaction_date: date
    fees: Optional[Decimal] = Field(default=0, ge=0)
    notes: Optional[str] = None


class TransactionCreate(TransactionBase):
    pass


class Transaction(TransactionBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class InvestmentAmountBase(BaseModel):
    amount: Decimal = Field(..., gt=0)
    date: date
    notes: Optional[str] = None


class InvestmentAmountCreate(InvestmentAmountBase):
    pass


class InvestmentAmount(InvestmentAmountBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class PortfolioSummary(BaseModel):
    total_value: Decimal
    total_invested: Decimal
    total_profit_loss: Decimal
    total_profit_loss_pct: Decimal
    positions: List[Position]

    class Config:
        from_attributes = True


class OptimizationMethod(str, Enum):
    """Supported optimization methods."""
    HRP = "hrp"
    EFFICIENT_FRONTIER = "ef"
    CVAR = "cvar"
    CLA = "cla"


class OptimizationRequest(BaseModel):
    """Request body for portfolio optimization."""
    tickers: List[str]
    start_date: date | None = None
    end_date: date | None = None
    method: OptimizationMethod
    risk_free_rate: float | None = 0.0
    constraints: dict | None = None  # e.g., {"min_weight": 0.0, "max_weight": 0.2}


class OptimizationResult(BaseModel):
    method: OptimizationMethod
    weights: dict[str, float]
    expected_return: float | None = None
    volatility: float | None = None
    sharpe_ratio: float | None = None

