"""Read financial statements and build their response data."""

from fastapi import HTTPException
from sqlalchemy import text

from app.schemas.financial_statements import FinancialStatementResponse, FinancialStatementItem, PeriodSummary


def _company(db, ticker):
    row = db.execute(text("SELECT company_id, name FROM company WHERE ticker = :ticker"), {"ticker": ticker}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Company {ticker} not found")
    return row


def get_financial_statements(ticker, statement_types, periods, max_periods, max_level, db):
    """
    Get financial statements for a company in hierarchical format
    
    Returns data organized by statement type with hierarchical line items
    and values across multiple periods.
    """
    company_id, company_name = _company(db, ticker)
    
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
    
    joins = """
        FROM company c
        JOIN item_value iv ON c.company_id = iv.company_id
        JOIN statement_item si ON iv.item_id = si.item_id
        JOIN statement s ON si.statement_id = s.statement_id
        JOIN period p ON iv.period_id = p.period_id
    """
    where_clause = " AND ".join(conditions)
    periods_result = db.execute(text(f"""
        SELECT DISTINCT p.label, p.end_date, p.period_type
        {joins}
        WHERE {where_clause}
        ORDER BY p.end_date DESC, p.label
        LIMIT :max_periods
    """), {**params, "max_periods": max_periods})
    periods_data = [
        PeriodSummary(label=row[0], end_date=row[1], period_type=row[2])
        for row in periods_result.fetchall()
    ]
    if not periods_data:
        raise HTTPException(status_code=404, detail=f"No financial data found for {ticker}")

    placeholders = [f":selected_period_{i}" for i in range(len(periods_data))]
    conditions.append(f"p.label IN ({', '.join(placeholders)})")
    params.update({f"selected_period_{i}": period.label for i, period in enumerate(periods_data)})
    where_clause = " AND ".join(conditions)
    rows = db.execute(text(f"""
        SELECT s.statement_type, s.title, si.item_id, si.item_key,
               si.title_vi, si.level, si.parent_item_id, si.display_order,
               p.label, p.end_date, iv.value
        {joins}
        WHERE {where_clause}
        ORDER BY s.statement_type, si.display_order, p.end_date DESC
    """), params).fetchall()

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


def get_statements_summary(ticker, db):
    """Get a summary of available financial statements for a company"""
    
    company_id, company_name = _company(db, ticker)
    
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


def get_companies_with_financial_data(db):
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


def get_available_periods(db):
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
