"""
Financial Statements API routes

Provides endpoints for retrieving and displaying financial statement data
in a hierarchical format similar to Vietnamese financial reporting standards.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.schemas.financial_statements import FinancialStatementResponse
from app.services import financial_statements

router = APIRouter(prefix="/financial", tags=["financial-statements"])


@router.get("/companies/{ticker}/statements", response_model=FinancialStatementResponse)
def get_financial_statements(
    ticker: str,
    statement_types: Optional[List[str]] = Query(
        None, 
        description="Filter by statement types: candoiketoan, baocaothunhap, luuchuyentiente, thuyetminh"
    ),
    periods: Optional[List[str]] = Query(
        None,
        description="Filter by specific periods (e.g., Q1-2025, Q2-2024)"
    ),
    max_periods: int = Query(8, ge=1, le=100, description="Maximum number of periods to return"),
    max_level: int = Query(5, ge=1, description="Maximum hierarchy level to return"),
    db: Session = Depends(get_db)
):
    '\n    Get financial statements for a company in hierarchical format\n    \n    Returns data organized by statement type with hierarchical line items\n    and values across multiple periods.\n    '
    return financial_statements.get_financial_statements(ticker, statement_types, periods, max_periods, max_level, db)


@router.get("/companies/{ticker}/statements/summary")
def get_statements_summary(
    ticker: str,
    db: Session = Depends(get_db)
):
    'Get a summary of available financial statements for a company'
    return financial_statements.get_statements_summary(ticker, db)


@router.get("/companies")
def get_companies_with_financial_data(
    db: Session = Depends(get_db)
):
    'Get all companies that have financial data'
    return financial_statements.get_companies_with_financial_data(db)


@router.get("/periods")
def get_available_periods(
    db: Session = Depends(get_db)
):
    'Get all available reporting periods'
    return financial_statements.get_available_periods(db)
