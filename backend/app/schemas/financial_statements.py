"""
Pydantic schemas for Financial Statements API
"""

from typing import List, Optional, Dict, Any
from datetime import date
from pydantic import BaseModel


class PeriodSummary(BaseModel):
    """Summary information for a reporting period"""
    label: str  # e.g., "Q1-2025"
    end_date: Optional[date]
    period_type: str  # quarter, year, month, other


class FinancialStatementItem(BaseModel):
    """Individual line item in a financial statement"""
    item_id: int
    item_key: str
    title_vi: str  # Vietnamese title
    level: int  # Hierarchy level (1 = top level, 2 = sub-item, etc.)
    parent_item_id: Optional[int]
    display_order: Optional[int]
    values: Dict[str, Optional[float]]  # period_label -> value mapping


class FinancialStatement(BaseModel):
    """A complete financial statement (Balance Sheet, Income Statement, etc.)"""
    statement_type: str  # candoiketoan, baocaothunhap, luuchuyentiente, thuyetminh
    title: str  # Human-readable title
    items: List[FinancialStatementItem]


class FinancialStatementResponse(BaseModel):
    """Complete financial statements response for a company"""
    company_ticker: str
    company_name: str
    periods: List[PeriodSummary]  # Available periods, most recent first
    statements: List[FinancialStatement]


class StatementSummary(BaseModel):
    """Summary of available statements for a company"""
    statement_type: str
    title: str
    period_count: int
    item_count: int
    earliest_period: Optional[date]
    latest_period: Optional[date]


class CompanyStatementsSummary(BaseModel):
    """Summary response for company's available financial statements"""
    company_ticker: str
    company_name: str
    statements: List[StatementSummary]
