# Vietnamese Financial Data Storage Schema

This project provides a PostgreSQL database schema and Python tools for storing Vietnamese financial statement data with hierarchical structure.

## Overview

The schema is designed to handle:
- Vietnamese financial statements (Balance Sheet, Income Statement, Cash Flow, Notes)
- Hierarchical line items with parent-child relationships
- Time series data across multiple reporting periods
- Multiple companies and reports

## Database Schema

### Core Tables

1. **company** - Companies being reported on
2. **period** - Reporting periods (quarters, years, etc.)
3. **report** - Container for complete financial report submissions
4. **statement** - Financial statement types within a report
5. **statement_item** - Individual line items with hierarchical structure
6. **item_value** - Actual numeric values for each line item per period

### Key Features

- **Hierarchical Structure**: Uses adjacency list model for parent-child relationships
- **Time Series Support**: Flexible period management with exact dates
- **Normalization**: Avoids data duplication while maintaining performance
- **Vietnamese Support**: Full UTF-8 support for Vietnamese text
- **Scalability**: Designed to handle multiple companies and years of data

## Setup Instructions

### 1. Database Setup

```bash
# Create PostgreSQL database
createdb financial_db

# Run the schema creation script
psql -d financial_db -f financial_schema.sql

# Optionally run sample data insertion
psql -d financial_db -f insert_sample_data.sql
```

### 2. Python Requirements

```bash
pip install psycopg2-binary
```

### 3. Configuration

Update the database configuration in `json_to_db_importer.py`:

```python
db_config = {
    'host': 'localhost',
    'database': 'financial_db',
    'user': 'your_username',
    'password': 'your_password',
    'port': 5432
}
```

## Usage

### JSON Data Format

Your JSON data should follow this structure:

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

### Import Data

```python
from json_to_db_importer import FinancialDataImporter
import json

# Load your JSON data
with open('your_financial_data.json', 'r', encoding='utf-8') as f:
    json_data = json.load(f)

# Configure database
db_config = {
    'host': 'localhost',
    'database': 'financial_db',
    'user': 'username',
    'password': 'password'
}

# Import data
importer = FinancialDataImporter(db_config)
importer.import_financial_data(
    json_data=json_data,
    company_ticker='VIC',
)
```

## Sample Queries

### 1. Get Balance Sheet Hierarchy

```sql
SELECT 
    REPEAT('  ', si.level - 1) || si.title_vi as indented_title,
    si.level,
    iv.value,
    p.label as period
FROM statement_item si
JOIN statement s ON si.statement_id = s.statement_id
JOIN item_value iv ON si.item_id = iv.item_id
JOIN period p ON iv.period_id = p.period_id
WHERE s.statement_type = 'candoiketoan'
    AND p.label = 'Q2-2025'
ORDER BY si.display_order;
```

### 2. Get Time Series for Specific Item

```sql
SELECT 
    p.label,
    iv.value,
    si.title_vi
FROM item_value iv
JOIN statement_item si ON iv.item_id = si.item_id
JOIN period p ON iv.period_id = p.period_id
WHERE si.item_key = 'tongcongtaisan'
ORDER BY p.end_date DESC;
```

### 3. Financial Overview Using View

```sql
SELECT 
    period_label,
    statement_type,
    level,
    title_vi,
    value
FROM v_financial_data
WHERE ticker = 'VIC'
    AND level <= 2  -- Only top 2 levels for summary
ORDER BY period_label DESC, statement_type, level;
```

### 4. Compare Values Across Periods

```sql
WITH quarterly_data AS (
    SELECT 
        si.title_vi,
        p.label,
        iv.value,
        LAG(iv.value) OVER (PARTITION BY si.item_id ORDER BY p.end_date) as prev_value
    FROM statement_item si
    JOIN item_value iv ON si.item_id = iv.item_id  
    JOIN period p ON iv.period_id = p.period_id
    WHERE si.item_key = 'tongcongtaisan'
)
SELECT 
    title_vi,
    label,
    value,
    prev_value,
    ROUND(((value - prev_value) / prev_value * 100), 2) as pct_change
FROM quarterly_data
WHERE prev_value IS NOT NULL
ORDER BY label DESC;
```

## Views Available

### v_statement_hierarchy
Recursive view showing the complete hierarchy of statement items with full paths.

### v_financial_data  
Complete financial data view joining all tables for easy querying.

## Data Types and Scale

- **Values**: Stored as `NUMERIC(20,1)` to handle large Vietnamese currency amounts
- **Currency**: Defaults to 'VND'
- **Unit Scale**: Configurable (1=units, 1000=thousands, 1000000=millions)
- **Dates**: Full date support for precise period management

## Performance Considerations

- Indexes on commonly queried columns (item_key, period, company)
- Proper foreign key relationships for referential integrity
- Normalized design to minimize data duplication
- Views for complex queries to improve readability

## Extending the Schema

To add new statement types:

1. Add the new type to the CHECK constraint in the `statement` table
2. Update the statement titles mapping in the Python importer
3. Ensure your JSON data includes the new statement type

To add new fields:

1. Add columns to the appropriate tables
2. Update the Python importer to handle the new fields
3. Update any relevant views

## License

This schema and tooling is provided as-is for educational and commercial use.