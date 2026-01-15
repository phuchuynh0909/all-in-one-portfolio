"""Schemas for price alerts."""
from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, Field
from enum import Enum


class AlertCondition(str, Enum):
    """Supported comparison operators for price alerts."""
    GT = "gt"      # Greater than
    GTE = "gte"    # Greater than or equal
    LT = "lt"      # Less than
    LTE = "lte"    # Less than or equal
    EQ = "eq"      # Equal


class PriceAlertBase(BaseModel):
    """Base schema for price alerts."""
    symbol: str = Field(..., min_length=1, max_length=20, description="Stock symbol")
    condition: AlertCondition = Field(..., description="Comparison operator (gt, gte, lt, lte, eq)")
    target_price: Decimal = Field(..., gt=0, description="Target price to compare against")
    notes: Optional[str] = Field(None, description="Optional notes for the alert")


class PriceAlertCreate(PriceAlertBase):
    """Schema for creating a new price alert."""
    pass


class PriceAlertUpdate(BaseModel):
    """Schema for updating a price alert."""
    symbol: Optional[str] = Field(None, min_length=1, max_length=20)
    condition: Optional[AlertCondition] = None
    target_price: Optional[Decimal] = Field(None, gt=0)
    is_active: Optional[bool] = None
    notes: Optional[str] = None


class PriceAlert(PriceAlertBase):
    """Schema for price alert response."""
    id: int
    is_active: bool
    is_triggered: bool
    triggered_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PriceAlertWithPrice(PriceAlert):
    """Price alert with current price information."""
    current_price: Optional[Decimal] = None
    price_diff: Optional[Decimal] = None  # Difference from target
    price_diff_pct: Optional[Decimal] = None  # Percentage difference from target


class PriceAlertsResponse(BaseModel):
    """Response for listing price alerts."""
    alerts: List[PriceAlertWithPrice]
    total: int

