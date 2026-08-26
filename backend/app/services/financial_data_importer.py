"""
Financial Data JSON to Database Importer

This service imports Vietnamese financial statement JSON data into the database
using the existing application infrastructure.
"""

import json
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

from app.core.settings import settings
from app.db.base import get_db

logger = logging.getLogger(__name__)


class FinancialDataImporter:
    # Global cache for statement mappings (master data)
    _statement_cache: Dict[str, int] = {}
    _cache_initialized: bool = False
    
    def __init__(self, db: Session):
        """Initialize with database session"""
        self.db = db

    def _upsert_returning_id(
        self,
        table: str,
        pk: str,
        values: Dict[str, Any],
        update_columns: List[str],
    ) -> int:
        """Upsert one row and return its primary key.

        MySQL has neither ``ON CONFLICT`` nor ``RETURNING`` — the two things the
        importer's original SQLite/Postgres statements relied on. The equivalent
        is ``ON DUPLICATE KEY UPDATE`` plus ``LAST_INSERT_ID()``.

        The ``pk = LAST_INSERT_ID(pk)`` assignment is the load-bearing part: on
        the insert path ``LAST_INSERT_ID()`` already returns the new autoincrement
        id, but on the duplicate path it would otherwise be stale. Assigning the
        existing row's key through ``LAST_INSERT_ID(expr)`` sets the session
        value to it, so the following ``SELECT`` returns the right id either way.

        ``update_columns`` is what an existing row gets refreshed to; the unique
        key that caused the conflict is never in it.
        """
        columns = list(values)
        col_list = ", ".join(f"`{c}`" for c in columns)
        placeholders = ", ".join(f":{c}" for c in columns)
        assignments = [f"`{c}` = VALUES(`{c}`)" for c in update_columns]
        assignments.append(f"`{pk}` = LAST_INSERT_ID(`{pk}`)")

        self.db.execute(
            text(
                f"INSERT INTO `{table}` ({col_list}) VALUES ({placeholders}) "
                f"ON DUPLICATE KEY UPDATE {', '.join(assignments)}"
            ),
            values,
        )
        return int(self.db.execute(text("SELECT LAST_INSERT_ID()")).scalar())


    def parse_period_label(self, period_label: str) -> Dict[str, Any]:
        """
        Parse period label like 'Q2-2025' into period details
        Returns dict with period_type, start_date, end_date
        """
        try:
            if period_label.startswith('Q') and '-' in period_label:
                quarter_str, year_str = period_label.split('-')
                quarter = int(quarter_str[1:])  # Remove 'Q' prefix
                year = int(year_str)
                
                # Calculate quarter dates
                quarter_starts = {
                    1: (1, 1), 2: (4, 1), 3: (7, 1), 4: (10, 1)
                }
                quarter_ends = {
                    1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)
                }
                
                start_month, start_day = quarter_starts[quarter]
                end_month, end_day = quarter_ends[quarter]
                
                return {
                    'period_type': 'quarter',
                    'start_date': f'{year}-{start_month:02d}-{start_day:02d}',
                    'end_date': f'{year}-{end_month:02d}-{end_day:02d}'
                }
            else:
                return {
                    'period_type': 'other',
                    'start_date': None,
                    'end_date': None
                }
        except Exception as e:
            logger.warning(f"Could not parse period {period_label}: {e}")
            return {
                'period_type': 'other',
                'start_date': None,
                'end_date': None
            }
    
    def get_company_by_ticker(self, ticker: str) -> int:
        """Find existing company by ticker"""
        result = self.db.execute(text("""
            SELECT company_id FROM company WHERE ticker = :ticker
        """), {"ticker": ticker})
        
        row = result.fetchone()
        if row:
            company_id = row[0]
            logger.info(f"Found existing company {ticker} with ID {company_id}")
            return company_id
        else:
            raise ValueError(f"Company with ticker '{ticker}' not found. Please ensure the company exists in the database first.")
    
    def insert_company(self, ticker: str, name: str) -> int:
        """Insert or get company ID - kept for backward compatibility"""
        try:
            return self.get_company_by_ticker(ticker)
        except ValueError:
            # If company doesn't exist, create it
            company_id = self._upsert_returning_id(
                "company",
                "company_id",
                {"ticker": ticker, "name": name},
                ["name"],
            )
            logger.info(f"Created new company {ticker} with ID {company_id}")
            return company_id
    
    def insert_periods(self, time_labels: List[str]) -> Dict[str, int]:
        """Insert periods and return mapping of label to period_id"""
        period_mapping = {}
        
        for label in time_labels:
            period_info = self.parse_period_label(label)
            
            period_id = self._upsert_returning_id(
                "period",
                "period_id",
                {
                    "label": label,
                    "period_type": period_info['period_type'],
                    "start_date": period_info['start_date'],
                    "end_date": period_info['end_date'],
                },
                ["period_type", "start_date", "end_date"],
            )
            period_mapping[label] = period_id
            
        logger.info(f"Inserted/updated {len(period_mapping)} periods")
        return period_mapping
    
    def insert_statements(self, statement_data: Dict[str, List]) -> Dict[str, int]:
        """Insert statements as master data and return mapping of type to statement_id"""
        statement_mapping = {}
        
        statement_titles = {
            'candoiketoan': 'Bảng cân đối kế toán',
            'baocaothunhap': 'Báo cáo thu nhập', 
            'luuchuyentiente': 'Báo cáo lưu chuyển tiền tệ',
            'thuyetminh': 'Thuyết minh báo cáo tài chính'
        }
        
        for statement_type, items in statement_data.items():
            if statement_type in ['time', 'type', 'total', 'kiemtoan']:
                continue  # Skip metadata
                
            if not items:  # Skip empty statements
                continue
                
            # Insert or get existing statement (master data)
            statement_id = self._upsert_returning_id(
                "statement",
                "statement_id",
                {
                    "statement_type": statement_type,
                    "title": statement_titles.get(statement_type),
                },
                ["title"],
            )
            statement_mapping[statement_type] = statement_id
            
        logger.info(f"Inserted/updated {len(statement_mapping)} statements")
        return statement_mapping
    
    def _initialize_statement_cache(self):
        """Initialize the statement cache by loading existing statements from database"""
        if self._cache_initialized:
            return
            
        try:
            result = self.db.execute(text("""
                SELECT statement_type, statement_id FROM statement
            """))
            
            for statement_type, statement_id in result.fetchall():
                self._statement_cache[statement_type] = statement_id
            
            self._cache_initialized = True
            logger.info(f"Statement cache initialized with {len(self._statement_cache)} statements")
            
        except Exception as e:
            logger.warning(f"Could not initialize statement cache: {e}")
            self._cache_initialized = False
    
    def get_or_create_statements(self, statement_data: Dict[str, List]) -> Dict[str, int]:
        """Get cached statements or create new ones if needed"""
        
        # Initialize cache if not done yet
        self._initialize_statement_cache()
        
        # Check which statements we need but don't have cached
        required_statements = set()
        for statement_type, items in statement_data.items():
            if statement_type not in ['time', 'type', 'total', 'kiemtoan'] and items:
                required_statements.add(statement_type)
        
        missing_statements = required_statements - set(self._statement_cache.keys())
        
        # Create missing statements
        if missing_statements:
            logger.info(f"Creating missing statements: {missing_statements}")
            
            statement_titles = {
                'candoiketoan': 'Bảng cân đối kế toán',
                'baocaothunhap': 'Báo cáo thu nhập', 
                'luuchuyentiente': 'Báo cáo lưu chuyển tiền tệ',
                'thuyetminh': 'Thuyết minh báo cáo tài chính'
            }
            
            for statement_type in missing_statements:
                statement_id = self._upsert_returning_id(
                    "statement",
                    "statement_id",
                    {
                        "statement_type": statement_type,
                        "title": statement_titles.get(statement_type),
                    },
                    ["title"],
                )
                self._statement_cache[statement_type] = statement_id
                logger.info(f"Created statement {statement_type} with ID {statement_id}")
        
        # Return only the statements that are actually needed
        return {stmt_type: self._statement_cache[stmt_type] 
                for stmt_type in required_statements if stmt_type in self._statement_cache}
    
    @classmethod
    def clear_statement_cache(cls):
        """Clear the statement cache (useful for testing or when statements change)"""
        cls._statement_cache.clear()
        cls._cache_initialized = False
        logger.info("Statement cache cleared")
    
    def warm_statement_cache(self):
        """Pre-warm the statement cache by loading all existing statements"""
        self._initialize_statement_cache()
        logger.info(f"Statement cache warmed with {len(self._statement_cache)} statements: {list(self._statement_cache.keys())}")
        return self._statement_cache.copy()
    
    def insert_statement_items(self, statement_id: int, items: List[Dict], 
                             time_labels: List[str]) -> Dict[str, int]:
        """Insert statement items and return mapping of key to item_id"""
        item_mapping = {}
        
        # First pass: insert all items without parent relationships
        for idx, item in enumerate(items):
            item_id = self._upsert_returning_id(
                "statement_item",
                "item_id",
                {
                    "statement_id": statement_id,
                    "item_key": item['key'],
                    "title_vi": item['title'],
                    "level": item['level'],
                    "display_order": idx,
                },
                ["title_vi", "level", "display_order"],
            )
            item_mapping[item['key']] = item_id
        
        # Second pass: establish parent relationships
        self._establish_item_hierarchy(statement_id, items, item_mapping)
        
        logger.info(f"Inserted {len(item_mapping)} statement items")
        return item_mapping
    
    def _establish_item_hierarchy(self, statement_id: int, items: List[Dict], 
                                item_mapping: Dict[str, int]):
        """Establish parent-child relationships for statement items"""
        
        # Create a stack to track parent items at each level
        parent_stack = [None] * 6  # Support up to level 5
        
        for item in items:
            level = item['level']
            item_id = item_mapping[item['key']]
            
            # Find parent (item at level - 1)
            parent_id = None
            if level > 1:
                parent_id = parent_stack[level - 1]
            
            # Update parent relationship
            self.db.execute(text("""
                UPDATE statement_item 
                SET parent_item_id = :parent_id 
                WHERE item_id = :item_id
            """), {"parent_id": parent_id, "item_id": item_id})
            
            # Update the parent stack
            parent_stack[level] = item_id
            # Clear deeper levels
            for i in range(level + 1, len(parent_stack)):
                parent_stack[i] = None
    
    def insert_item_values(self, company_id: int, item_mapping: Dict[str, int], 
                          period_mapping: Dict[str, int], items: List[Dict], time_labels: List[str]):
        """Insert item values for all periods for a specific company"""
        
        for item in items:
            item_id = item_mapping[item['key']]
            
            # Extract values for each period (value1, value2, ..., value9)
            for idx, period_label in enumerate(time_labels, 1):
                value_key = f'value{idx}'
                if value_key in item and item[value_key] is not None:
                    try:
                        # Convert string to float, handle potential formatting
                        value_str = str(item[value_key]).replace(',', '')
                        if value_str and value_str not in ['None', 'null', '']:
                            value = float(value_str)
                            period_id = period_mapping[period_label]
                            
                            # MySQL's upsert; no id needed back here, so the
                            # LAST_INSERT_ID dance in ``_upsert_returning_id``
                            # would be wasted round trips.
                            self.db.execute(text("""
                                INSERT INTO item_value (item_id, period_id, company_id, value)
                                VALUES (:item_id, :period_id, :company_id, :value)
                                ON DUPLICATE KEY UPDATE value = VALUES(value)
                            """), {
                                "item_id": item_id,
                                "period_id": period_id,
                                "company_id": company_id,
                                "value": value
                            })
                    except (ValueError, KeyError) as e:
                        logger.warning(f"Could not parse value {item[value_key]} for item {item['key']}: {e}")
    
    def import_financial_data(self, json_data: Dict[str, Any], 
                            company_ticker: str):
        """Main method to import complete financial data"""
        
        try:
            # Find existing company by ticker
            company_id = self.get_company_by_ticker(company_ticker)
            
            # Insert periods
            time_labels = json_data['time']
            period_mapping = self.insert_periods(time_labels)
            
            # Get or create statements (using cache for performance)
            statement_mapping = self.get_or_create_statements(json_data)
            
            # Insert statement items and values for each statement type
            for statement_type, statement_id in statement_mapping.items():
                if statement_type in json_data and json_data[statement_type]:
                    items = json_data[statement_type]
                    
                    # Insert items (master data)
                    item_mapping = self.insert_statement_items(
                        statement_id, items, time_labels
                    )
                    
                    # Insert values for this company
                    self.insert_item_values(
                        company_id, item_mapping, period_mapping, items, time_labels
                    )
            
            # Commit transaction
            self.db.commit()
            logger.info(f"Financial data import completed successfully for {company_ticker}")
            
        except Exception as e:
            logger.error(f"Import failed for {company_ticker}: {e}")
            self.db.rollback()
            raise


# Convenience function for standalone usage
def import_financial_data_from_file(file_path: str, company_ticker: str):
    """Import financial data from JSON file"""
    
    with open(file_path, 'r', encoding='utf-8') as f:
        json_data = json.load(f)
    
    db = next(get_db())
    try:
        importer = FinancialDataImporter(db)
        importer.import_financial_data(json_data, company_ticker)
        print(f"Successfully imported financial data for {company_ticker}")
    except Exception as e:
        print(f"Import failed: {e}")
        raise
    finally:
        db.close()


# Example usage as script
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 4:
        print("Usage: python -m app.services.financial_data_importer <json_file> <ticker>")
        sys.exit(1)
    
    file_path = sys.argv[1]
    ticker = sys.argv[2]
    
    import_financial_data_from_file(file_path, ticker)
