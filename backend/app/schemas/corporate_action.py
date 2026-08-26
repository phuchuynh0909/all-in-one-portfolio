"""Request and response models for corporate actions."""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class CorporateActionOut(BaseModel):
    id: int
    symbol: str
    event_id: int
    name: str
    action_type: str
    ex_date: date
    record_date: Optional[date] = None
    pay_date: Optional[date] = None
    amount_per_share: Optional[Decimal] = None
    ratio: Optional[Decimal] = None
    tax_withheld_pct: Optional[Decimal] = None
    title: str
    url: Optional[str] = None
    source: str
    status: str
    applied_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ManualDividendCreate(BaseModel):
    """A dividend entered by hand — for what the feed missed or misparsed."""

    symbol: str = Field(..., min_length=1, max_length=20)
    action_type: Literal["cash", "stock"]
    ex_date: date
    amount_per_share: Optional[Decimal] = Field(default=None, gt=0)
    ratio: Optional[Decimal] = Field(default=None, gt=0)
    tax_withheld_pct: Optional[Decimal] = Field(default=None, ge=0, le=1)
    notes: Optional[str] = None


class SyncResult(BaseModel):
    inserted: int
    skipped: int
    unparsed: int
    ignored: int


class AppliedLot(BaseModel):
    position_id: Optional[int] = None
    qty_before: Decimal
    qty_after: Decimal
    price_before: Decimal
    price_after: Decimal
    shares_added: Decimal
    cash_amount: Optional[Decimal] = None
    transaction_id: Optional[int] = None


class ApplyResult(BaseModel):
    corporate_action_id: int
    # Every action settled in this call. Events sharing an ex-date must settle
    # together (see the engine), so one apply can cover several.
    applied_action_ids: List[int] = []
    status: str
    lots: List[AppliedLot]
    total_shares_added: Decimal
    total_cash_gross: Decimal
