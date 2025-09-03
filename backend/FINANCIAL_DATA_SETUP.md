# Financial Data Schema Setup Guide

This guide walks you through setting up and using the Vietnamese financial data storage system in your portfolio project.

## Overview

The financial data system provides:
- ✅ **Alembic Migration**: Database schema managed through your existing migration system
- ✅ **SQLAlchemy Models**: Full integration with your existing ORM setup
- ✅ **Import Service**: Python service to import JSON financial data
- ✅ **CLI Tool**: Command-line interface for data management
- ✅ **Hierarchical Support**: Proper parent-child relationships for financial line items
- ✅ **Time Series Storage**: Multi-period data storage and querying

## Quick Start

### 1. Run the Migration

```bash
# Navigate to backend directory
cd backend

# Run the migration to create tables
alembic upgrade head
```

This will create all the financial data tables:
- `company` - Companies being reported on
- `period` - Reporting periods (Q1-2025, etc.)
- `report` - Financial report containers
- `statement` - Statement types (Balance Sheet, Income, etc.)
- `statement_item` - Individual line items with hierarchy
- `item_value` - Actual financial values per period

### 2. Import Sample Data

```bash
# Create a sample JSON file with your financial data
# (Use the format from your provided JSON)

# Import using the CLI tool
python scripts/financial_data_cli.py import-data financial_data.json VIC "Vingroup JSC"

# Or import programmatically
python -c "
from app.services.financial_data_importer import import_financial_data_from_file
import_financial_data_from_file('financial_data.json', 'VIC')
"
```

### 3. Query the Data

```bash
# List all companies
python scripts/financial_data_cli.py list-companies

# Query specific company data
python scripts/financial_data_cli.py query-data VIC --period Q2-2025 --statement-type candoiketoan

# Validate schema setup
python scripts/financial_data_cli.py validate-schema
```

## Using in Your Application

### Import the Models

```python
from app.db.models.financial import (
    Company, Period, Report, Statement, StatementItem, ItemValue
)
```

### Query Examples

```python
from sqlalchemy.orm import Session
from app.db.models.financial import Company, Report, Statement, StatementItem, ItemValue, Period

def get_company_financials(db: Session, ticker: str, period_label: str = None):
    """Get financial data for a company"""
    query = db.query(
        Company.ticker,
        Period.label,
        Statement.statement_type,
        StatementItem.title_vi,
        StatementItem.level,
        ItemValue.value
    ).join(Report).join(Statement).join(StatementItem).join(ItemValue).join(Period)
    
    query = query.filter(Company.ticker == ticker)
    
    if period_label:
        query = query.filter(Period.label == period_label)
    
    return query.order_by(Period.end_date.desc(), StatementItem.display_order).all()


def get_balance_sheet_hierarchy(db: Session, ticker: str, period_label: str):
    """Get balance sheet with proper hierarchy"""
    return db.query(
        StatementItem.level,
        StatementItem.title_vi,
        ItemValue.value,
        StatementItem.parent_item_id
    ).join(Statement).join(Report).join(Company).join(ItemValue).join(Period)\
     .filter(Company.ticker == ticker)\
     .filter(Period.label == period_label)\
     .filter(Statement.statement_type == 'candoiketoan')\
     .order_by(StatementItem.display_order).all()
```

### Using the Views

```python
# Use the pre-built views for complex queries
def get_financial_overview(db: Session, ticker: str):
    """Get complete financial overview using the view"""
    return db.execute("""
        SELECT period_label, statement_type, level, title_vi, value, currency, unit_scale
        FROM v_financial_data 
        WHERE ticker = :ticker AND level <= 2
        ORDER BY period_label DESC, statement_type, level
    """, {"ticker": ticker}).fetchall()
```

## CLI Commands Reference

### Import Data
```bash
# Import financial data from JSON
python scripts/financial_data_cli.py import-data <json_file> <ticker> <company_name>

# Dry run (validate without importing)
python scripts/financial_data_cli.py import-data --dry-run <json_file> <ticker> <company_name>
```

### Query Data
```bash
# Query all data for a company
python scripts/financial_data_cli.py query-data <ticker>

# Query specific period
python scripts/financial_data_cli.py query-data <ticker> --period Q2-2025

# Query specific statement type
python scripts/financial_data_cli.py query-data <ticker> --statement-type candoiketoan

# Limit hierarchy depth
python scripts/financial_data_cli.py query-data <ticker> --max-level 2
```

### Management
```bash
# List all companies
python scripts/financial_data_cli.py list-companies

# Validate schema
python scripts/financial_data_cli.py validate-schema
```

## JSON Data Format

Your JSON should follow this structure:

```json
{
    "candoiketoan": [
        {
            "title": "A. TÀI SẢN NGẮN HẠN",
            "key": "taisannganhan",
            "level": 2,
            "value1": "17,503.0",
            "value2": "17,270.0",
            ...
        }
    ],
    "baocaothunhap": [...],
    "luuchuyentiente": [...],
    "thuyetminh": [...],
    "time": ["Q2-2025", "Q1-2025", "Q4-2024", ...],
    "type": 1,
    "total": 6
}
```

## API Integration

### Create API Endpoints

You can create FastAPI endpoints to expose the financial data:

```python
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.base import get_db
from app.db.models.financial import Company

router = APIRouter(prefix="/api/v1/financial", tags=["financial"])

@router.get("/companies")
def list_companies(db: Session = Depends(get_db)):
    """List all companies with financial data"""
    return db.query(Company).all()

@router.get("/companies/{ticker}/financials")
def get_company_financials(ticker: str, period: str = None, db: Session = Depends(get_db)):
    """Get financial data for a company"""
    # Implementation here
    pass
```

## Performance Considerations

1. **Indexes**: The migration creates optimized indexes for common queries
2. **Views**: Use the pre-built views for complex hierarchical queries
3. **Batch Operations**: The importer uses bulk operations for large datasets
4. **Memory Usage**: For large imports, consider processing in chunks

## Extending the Schema

### Adding New Statement Types

1. Add to the check constraint in the migration:
```python
sa.CheckConstraint("statement_type IN ('candoiketoan','baocaothunhap','luuchuyentiente','thuyetminh','new_type')")
```

2. Update the statement titles mapping in the importer

### Adding New Fields

1. Create a new migration:
```bash
alembic revision -m "add_new_field"
```

2. Add the column:
```python
def upgrade():
    op.add_column('report', sa.Column('new_field', sa.String(), nullable=True))
```

## Troubleshooting

### Migration Issues
```bash
# Check current migration status
alembic current

# See pending migrations
alembic heads

# Rollback if needed
alembic downgrade -1
```

### Import Issues
```bash
# Validate JSON format first
python scripts/financial_data_cli.py import-data --dry-run your_file.json TICKER "Company Name"

# Check database connection
python scripts/financial_data_cli.py validate-schema
```

### Query Issues
```bash
# Check if data exists
python scripts/financial_data_cli.py list-companies

# Validate specific company data
python scripts/financial_data_cli.py query-data YOUR_TICKER
```

This setup provides a complete, production-ready financial data management system integrated with your existing portfolio application!
