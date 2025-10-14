from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, validator


class ConditionOperator(str, Enum):
    eq = "eq"
    ne = "ne"
    gt = "gt"
    gte = "gte"
    lt = "lt"
    lte = "lte"
    isin = "in"
    notin = "notin"
    between = "between"
    contains = "contains"


class Condition(BaseModel):
    column: str = Field(..., description="Feature column name")
    operator: ConditionOperator = Field(..., description="Comparison operator")
    value: Any = Field(..., description="Comparison value. For 'between', provide [min, max]. For 'in', provide a list.")

    @validator("value")
    def validate_value(cls, v: Any, values: Dict[str, Any]) -> Any:
        op = values.get("operator")
        if op in (ConditionOperator.isin, ConditionOperator.notin):
            if not isinstance(v, list):
                raise ValueError("For 'in'/'notin', value must be a list")
        if op == ConditionOperator.between:
            if not (isinstance(v, list) and len(v) == 2):
                raise ValueError("For 'between', value must be a list of two elements [min, max]")
        return v


class ScannerRequest(BaseModel):
    conditions: List[Condition] = Field(default_factory=list)
    columns_to_return: Optional[List[str]] = Field(
        default=None, description="Additional columns to include in response"
    )
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    symbols: Optional[List[str]] = None
    latest_only: bool = Field(default=True, description="Return only the latest date per symbol")


class ScannerResultItem(BaseModel):
    symbol: str
    date: datetime
    values: Dict[str, Any] = Field(default_factory=dict)


class ScannerResponse(BaseModel):
    items: List[ScannerResultItem]
    total: int


class ScannerColumnsResponse(BaseModel):
    columns: List[str]


