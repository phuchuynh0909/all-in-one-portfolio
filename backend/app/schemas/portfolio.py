from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, Field


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
