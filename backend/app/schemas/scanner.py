from __future__ import annotations

from datetime import date, datetime
from enum import Enum
import math
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


SCANNER_COLUMNS: List[str] = [
    "date", "symbol", "open", "high", "low", "close", "volume",
    "rsi_5", "rsi_14", "ema_10", "ema_20", "ema_50", "ema_200", "atr_10", "atr_14", "obv",
    "reg", "pi_upper", "pi_lower", "ci_upper", "ci_lower", "slope_pct", "reclaim_entry",
    "frama", "long_v", "short_v", "qb", "hso", "os", "trail",
]


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

    @field_validator("column")
    @classmethod
    def validate_column(cls, value: str) -> str:
        if value not in SCANNER_COLUMNS:
            raise ValueError("Unknown scanner column")
        return value

    @model_validator(mode="after")
    def validate_value(self):
        op, value = self.operator, self.value
        if op == ConditionOperator.contains:
            if not isinstance(value, str):
                raise ValueError("Contains requires a string pattern")
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError("Invalid contains pattern") from exc
            return self

        def scalar(item):
            if self.column == "symbol":
                if not isinstance(item, str):
                    raise ValueError("Symbol comparisons require strings")
                return item
            if self.column == "date":
                if not isinstance(item, str):
                    raise ValueError("Date comparisons require YYYY-MM-DD strings")
                try:
                    return datetime.combine(date.fromisoformat(item), datetime.min.time())
                except ValueError as exc:
                    raise ValueError("Invalid comparison date") from exc
            if isinstance(item, bool) or not isinstance(item, (int, float, str)):
                raise ValueError("Numeric comparisons require finite numbers")
            try:
                number = float(item)
            except (ValueError, OverflowError) as exc:
                raise ValueError("Numeric comparisons require finite numbers or a compatible column") from exc
            if not math.isfinite(number):
                raise ValueError("Numeric comparisons require finite numbers")
            return number

        if op in (ConditionOperator.isin, ConditionOperator.notin, ConditionOperator.between):
            if not isinstance(value, list) or (op == ConditionOperator.between and len(value) != 2):
                raise ValueError("Membership requires a list; between requires two bounds")
            self.value = [scalar(item) for item in value]
            if op == ConditionOperator.between and self.value[0] > self.value[1]:
                raise ValueError("Between bounds must be in ascending order")
        elif isinstance(value, str) and value in SCANNER_COLUMNS:
            if self.column != value and ({self.column, value} & {"symbol", "date"}):
                raise ValueError("Comparison columns must have compatible types")
        else:
            self.value = scalar(value)
        return self


class ScannerRequest(BaseModel):
    conditions: List[Condition] = Field(default_factory=list)
    columns_to_return: Optional[List[str]] = Field(
        default=None, description="Additional columns to include in response"
    )
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    symbols: Optional[List[str]] = None
    latest_only: bool = Field(default=True, description="Return only the latest date per symbol")

    @field_validator("columns_to_return")
    @classmethod
    def validate_requested_columns(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value and any(column not in SCANNER_COLUMNS for column in value):
            raise ValueError("Unknown scanner column")
        return value


class ScannerResultItem(BaseModel):
    symbol: str
    date: datetime
    values: Dict[str, Any] = Field(default_factory=dict)


class ScannerResponse(BaseModel):
    items: List[ScannerResultItem]
    total: int


class ScannerColumnsResponse(BaseModel):
    columns: List[str]




