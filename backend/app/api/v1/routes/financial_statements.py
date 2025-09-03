"""
Financial Statements API routes

Provides endpoints for retrieving and displaying financial statement data
in a hierarchical format similar to Vietnamese financial reporting standards.
"""

from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.base import get_db
from app.schemas.financial_statements import (
    FinancialStatementResponse,
    FinancialStatementItem,
    PeriodSummary
)

router = APIRouter(prefix="/financial", tags=["financial-statements"])


@router.get("/companies/{ticker}/statements", response_model=FinancialStatementResponse)
async def get_financial_statements(
    ticker: str,
    statement_types: Optional[List[str]] = Query(
        None, 
        description="Filter by statement types: candoiketoan, baocaothunhap, luuchuyentiente, thuyetminh"
    ),
    periods: Optional[List[str]] = Query(
        None,
        description="Filter by specific periods (e.g., Q1-2025, Q2-2024)"
    ),
    max_periods: int = Query(8, description="Maximum number of periods to return"),
    max_level: int = Query(5, description="Maximum hierarchy level to return"),
    db: Session = Depends(get_db)
):
    """
    Get financial statements for a company in hierarchical format
    
    Returns data organized by statement type with hierarchical line items
    and values across multiple periods.
    """
    print("Getting financial statements for company:", ticker)
    
    # Check if company exists
    company_result = db.execute(text("""
        SELECT company_id, name FROM company WHERE ticker = :ticker
    """), {"ticker": ticker})
    
    company_row = company_result.fetchone()
    if not company_row:
        raise HTTPException(status_code=404, detail=f"Company {ticker} not found")
    
    company_id, company_name = company_row
    
    # Build dynamic query conditions
    conditions = ["c.company_id = :company_id", "si.level <= :max_level"]
    params = {"company_id": company_id, "max_level": max_level}
    
    if statement_types:
        placeholders = [f":stmt_type_{i}" for i in range(len(statement_types))]
        conditions.append(f"s.statement_type IN ({', '.join(placeholders)})")
        for i, stmt_type in enumerate(statement_types):
            params[f"stmt_type_{i}"] = stmt_type
    
    if periods:
        placeholders = [f":period_{i}" for i in range(len(periods))]
        conditions.append(f"p.label IN ({', '.join(placeholders)})")
        for i, period in enumerate(periods):
            params[f"period_{i}"] = period
    
    where_clause = " AND ".join(conditions)
    
    # Get financial data
    query = f"""
        SELECT 
            s.statement_type,
            s.title as statement_title,
            si.item_id,
            si.item_key,
            si.title_vi,
            si.level,
            si.parent_item_id,
            si.display_order,
            p.label as period_label,
            p.end_date,
            iv.value
        FROM company c
        JOIN item_value iv ON c.company_id = iv.company_id
        JOIN statement_item si ON iv.item_id = si.item_id
        JOIN statement s ON si.statement_id = s.statement_id
        JOIN period p ON iv.period_id = p.period_id
        WHERE {where_clause}
        ORDER BY s.statement_type, si.display_order, p.end_date DESC
        LIMIT 10000
    """
    
    result = db.execute(text(query), params)
    rows = result.fetchall()
    
    if not rows:
        raise HTTPException(status_code=404, detail=f"No financial data found for {ticker}")
    
    # Get period list (most recent first)
    periods_query = f"""
        SELECT DISTINCT p.label, p.end_date, p.period_type
        FROM period p
        JOIN item_value iv ON p.period_id = iv.period_id
        WHERE iv.company_id = :company_id
        ORDER BY p.end_date DESC
        LIMIT :max_periods
    """
    
    periods_result = db.execute(text(periods_query), {
        "company_id": company_id, 
        "max_periods": max_periods
    })
    
    periods_data = [
        PeriodSummary(
            label=row[0],
            end_date=row[1],
            period_type=row[2]
        )
        for row in periods_result.fetchall()
    ]
    
    # Organize data by statement type
    statements_data = {}
    
    for row in rows:
        (stmt_type, stmt_title, item_id, item_key, title_vi, level, 
         parent_item_id, display_order, period_label, end_date, value) = row
        
        # Initialize statement if not exists
        if stmt_type not in statements_data:
            statements_data[stmt_type] = {
                "statement_type": stmt_type,
                "title": stmt_title,
                "items": {},
                "item_order": []
            }
        
        # Initialize item if not exists
        if item_id not in statements_data[stmt_type]["items"]:
            statements_data[stmt_type]["items"][item_id] = {
                "item_id": item_id,
                "item_key": item_key,
                "title_vi": title_vi,
                "level": level,
                "parent_item_id": parent_item_id,
                "display_order": display_order,
                "values": {}
            }
            statements_data[stmt_type]["item_order"].append(item_id)
        
        # Add value for this period
        statements_data[stmt_type]["items"][item_id]["values"][period_label] = value
    
    # Convert to response format
    statements = []
    for stmt_type, stmt_data in statements_data.items():
        # Sort items by display_order
        sorted_items = sorted(
            stmt_data["items"].values(),
            key=lambda x: (x["display_order"] or 0)
        )
        
        items = [
            FinancialStatementItem(
                item_id=item["item_id"],
                item_key=item["item_key"],
                title_vi=item["title_vi"],
                level=item["level"],
                parent_item_id=item["parent_item_id"],
                display_order=item["display_order"],
                values=item["values"]
            )
            for item in sorted_items
        ]
        
        statements.append({
            "statement_type": stmt_type,
            "title": stmt_data["title"],
            "items": items
        })
    
    return FinancialStatementResponse(
        company_ticker=ticker,
        company_name=company_name,
        periods=periods_data,
        statements=statements
    )


@router.get("/companies/{ticker}/statements/summary")
async def get_statements_summary(
    ticker: str,
    db: Session = Depends(get_db)
):
    """Get a summary of available financial statements for a company"""
    
    # Check if company exists
    company_result = db.execute(text("""
        SELECT company_id, name FROM company WHERE ticker = :ticker
    """), {"ticker": ticker})
    
    company_row = company_result.fetchone()
    if not company_row:
        raise HTTPException(status_code=404, detail=f"Company {ticker} not found")
    
    company_id, company_name = company_row
    
    # Get available statements and periods
    summary_query = """
        SELECT 
            s.statement_type,
            s.title,
            COUNT(DISTINCT p.label) as period_count,
            COUNT(DISTINCT si.item_id) as item_count,
            MIN(p.end_date) as earliest_period,
            MAX(p.end_date) as latest_period
        FROM company c
        JOIN item_value iv ON c.company_id = iv.company_id
        JOIN statement_item si ON iv.item_id = si.item_id
        JOIN statement s ON si.statement_id = s.statement_id
        JOIN period p ON iv.period_id = p.period_id
        WHERE c.company_id = :company_id
        GROUP BY s.statement_type, s.title
        ORDER BY s.statement_type
    """
    
    result = db.execute(text(summary_query), {"company_id": company_id})
    
    return {
        "company_ticker": ticker,
        "company_name": company_name,
        "statements": [
            {
                "statement_type": row[0],
                "title": row[1],
                "period_count": row[2],
                "item_count": row[3],
                "earliest_period": row[4],
                "latest_period": row[5]
            }
            for row in result.fetchall()
        ]
    }


@router.get("/companies")
async def get_companies_with_financial_data(
    db: Session = Depends(get_db)
):
    """Get all companies that have financial data"""
    
    query = """
        SELECT DISTINCT 
            c.ticker, 
            c.name,
            COUNT(DISTINCT iv.item_value_id) as data_points,
            COUNT(DISTINCT p.period_id) as periods_count,
            MIN(p.end_date) as earliest_period,
            MAX(p.end_date) as latest_period
        FROM company c
        JOIN item_value iv ON c.company_id = iv.company_id
        JOIN period p ON iv.period_id = p.period_id
        GROUP BY c.company_id, c.ticker, c.name
        HAVING COUNT(DISTINCT iv.item_value_id) > 0
        ORDER BY c.ticker
    """
    
    result = db.execute(text(query))
    
    return [
        {
            "ticker": row[0],
            "name": row[1],
            "data_points": row[2],
            "periods_count": row[3],
            "earliest_period": row[4],
            "latest_period": row[5]
        }
        for row in result.fetchall()
    ]


@router.get("/periods")
async def get_available_periods(
    db: Session = Depends(get_db)
):
    """Get all available reporting periods"""
    
    query = """
        SELECT DISTINCT p.label, p.period_type, p.start_date, p.end_date
        FROM period p
        JOIN item_value iv ON p.period_id = iv.period_id
        ORDER BY p.end_date DESC
    """
    
    result = db.execute(text(query))
    
    return [
        {
            "label": row[0],
            "period_type": row[1], 
            "start_date": row[2],
            "end_date": row[3]
        }
        for row in result.fetchall()
    ]
