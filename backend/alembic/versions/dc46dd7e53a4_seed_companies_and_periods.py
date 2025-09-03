"""seed_companies_and_periods

Revision ID: dc46dd7e53a4
Revises: 431c1ea23608
Create Date: 2025-09-02 21:44:04.287897

"""
from typing import Sequence, Union
import os
from datetime import date
from pathlib import Path

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import table, column


# revision identifiers, used by Alembic.
revision: str = 'dc46dd7e53a4'
down_revision: Union[str, None] = '431c1ea23608'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Define table structure for bulk inserts
    company_table = table('company',
        column('ticker', sa.String),
        column('name', sa.String)
    )
    
    period_table = table('period',
        column('label', sa.String),
        column('period_type', sa.String),
        column('start_date', sa.Date),
        column('end_date', sa.Date)
    )
    
    # 1. Seed companies from watchlist.csv
    companies_data = []
    watchlist_path = os.path.join(os.path.dirname(__file__), '..', '..', 'models', 'watchlist.csv')
    
    try:
        with open(watchlist_path, 'r', encoding='utf-8') as f:
            for line in f:
                ticker = line.strip()
                if ticker:  # Skip empty lines
                    companies_data.append({
                        'ticker': ticker,
                        'name': f"{ticker} Corporation"  # Create a simple company name
                    })
    except FileNotFoundError:
        print(f"Warning: Could not find watchlist.csv at {watchlist_path}")
        # Add some default companies if file not found
        companies_data = [
            {'ticker': 'VIC', 'name': 'Vingroup Joint Stock Company'},
            {'ticker': 'VHM', 'name': 'Vinhomes Joint Stock Company'},
            {'ticker': 'HPG', 'name': 'Hoa Phat Group Joint Stock Company'},
        ]
    
    # Insert companies
    if companies_data:
        op.bulk_insert(company_table, companies_data)
        print(f"Inserted {len(companies_data)} companies")
    
    # 2. Seed periods from Q1-2015 to Q4-2030
    periods_data = []
    
    # Generate quarterly periods
    for year in range(2015, 2031):  # 2015 to 2030 inclusive
        for quarter in range(1, 5):  # Q1, Q2, Q3, Q4
            # Calculate quarter dates
            quarter_starts = {
                1: (1, 1), 2: (4, 1), 3: (7, 1), 4: (10, 1)
            }
            quarter_ends = {
                1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)
            }
            
            start_month, start_day = quarter_starts[quarter]
            end_month, end_day = quarter_ends[quarter]
            
            periods_data.append({
                'label': f'Q{quarter}-{year}',
                'period_type': 'quarter',
                'start_date': date(year, start_month, start_day),
                'end_date': date(year, end_month, end_day)
            })
    
    # Insert periods
    if periods_data:
        op.bulk_insert(period_table, periods_data)
        print(f"Inserted {len(periods_data)} periods")


def downgrade() -> None:
    # Remove all seeded data
    op.execute("DELETE FROM item_value")
    op.execute("DELETE FROM statement_item") 
    op.execute("DELETE FROM statement")
    op.execute("DELETE FROM report")
    op.execute("DELETE FROM period")
    op.execute("DELETE FROM company")
