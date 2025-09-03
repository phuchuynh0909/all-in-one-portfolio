#!/usr/bin/env python3
"""
Financial Data CLI Tool

Command-line interface for managing financial data imports and migrations.
"""

import click
import json
import sys
import os
from pathlib import Path

# Add the backend directory to the Python path
sys.path.append(str(Path(__file__).parent.parent))

from app.services.financial_data_importer import import_financial_data_from_file
from app.db.base import get_db
from app.services.financial_data_importer import FinancialDataImporter
from sqlalchemy import text


@click.group()
def cli():
    """Financial Data Management CLI"""
    pass


@cli.command()
@click.argument('ticker')
@click.argument('company_name')
def add_company(ticker: str, company_name: str):
    """Add a new company to the database"""
    
    try:
        db = next(get_db())
        
        # Check if company already exists
        result = db.execute(text("SELECT company_id, name FROM company WHERE ticker = :ticker"), {"ticker": ticker})
        existing = result.fetchone()
        
        if existing:
            click.echo(f"❌ Company {ticker} already exists: {existing[1]}")
            return
        
        # Insert new company
        result = db.execute(text("""
            INSERT INTO company (ticker, name) 
            VALUES (:ticker, :name) 
            RETURNING company_id
        """), {"ticker": ticker, "name": company_name})
        
        company_id = result.fetchone()[0]
        db.commit()
        
        click.echo(f"✅ Added company: {ticker} - {company_name} (ID: {company_id})")
        
    except Exception as e:
        click.echo(f"❌ Failed to add company: {e}", err=True)
        sys.exit(1)
    finally:
        db.close()


@cli.command()
@click.argument('json_file', type=click.Path(exists=True))
@click.argument('ticker')
@click.option('--dry-run', is_flag=True, help='Validate JSON without importing')
def import_data(json_file: str, ticker: str, dry_run: bool):
    """Import financial data from JSON file (company must exist)"""
    
    try:
        # Load and validate JSON
        click.echo(f"Loading JSON file: {json_file}")
        with open(json_file, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
        
        # Basic validation
        required_keys = ['time', 'candoiketoan', 'baocaothunhap', 'luuchuyentiente']
        missing_keys = [key for key in required_keys if key not in json_data]
        if missing_keys:
            click.echo(f"❌ Missing required keys: {missing_keys}", err=True)
            sys.exit(1)
        
        periods = json_data.get('time', [])
        click.echo(f"📊 Found {len(periods)} periods: {', '.join(periods)}")
        
        # Count items in each statement
        for stmt_type in ['candoiketoan', 'baocaothunhap', 'luuchuyentiente', 'thuyetminh']:
            if stmt_type in json_data:
                count = len(json_data[stmt_type]) if json_data[stmt_type] else 0
                click.echo(f"  📋 {stmt_type}: {count} items")
        
        if dry_run:
            click.echo("✅ JSON validation passed. Use without --dry-run to import.")
            return
        
        # Check if company exists first
        db = next(get_db())
        try:
            importer = FinancialDataImporter(db)
            importer.import_financial_data(json_data, ticker)
            click.echo("✅ Import completed successfully!")
        finally:
            db.close()
            
    except json.JSONDecodeError as e:
        click.echo(f"❌ Invalid JSON file: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Import failed: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument('ticker')
@click.option('--period', help='Specific period to query (e.g., Q2-2025)')
@click.option('--statement-type', type=click.Choice(['candoiketoan', 'baocaothunhap', 'luuchuyentiente', 'thuyetminh']), 
              help='Specific statement type to query')
@click.option('--max-level', type=int, default=5, help='Maximum hierarchy level to display')
def query_data(ticker: str, period: str = None, statement_type: str = None, max_level: int = 5):
    """Query financial data for a company"""
    
    try:
        db = next(get_db())
        
        # Build query conditions
        conditions = ["c.ticker = :ticker"]
        params = {"ticker": ticker}
        
        if period:
            conditions.append("p.label = :period")
            params["period"] = period
        
        if statement_type:
            conditions.append("s.statement_type = :statement_type")
            params["statement_type"] = statement_type
            
        conditions.append("si.level <= :max_level")
        params["max_level"] = max_level
        
        where_clause = " AND ".join(conditions)
        
        query = f"""
        SELECT DISTINCT
            p.label as period,
            s.statement_type,
            si.title_vi,
            si.level,
            iv.value,
            si.display_order
        FROM company c
        JOIN item_value iv ON c.company_id = iv.company_id
        JOIN statement_item si ON iv.item_id = si.item_id
        JOIN statement s ON si.statement_id = s.statement_id
        JOIN period p ON iv.period_id = p.period_id
        WHERE {where_clause}
        ORDER BY p.end_date DESC, s.statement_type, si.display_order
        """
        
        result = db.execute(text(query), params)
        rows = result.fetchall()
        
        if not rows:
            click.echo(f"❌ No data found for ticker: {ticker}")
            return
        
        click.echo(f"📊 Financial data for {ticker}:")
        click.echo("=" * 80)
        
        current_period = None
        current_statement = None
        
        for row in rows:
            period, stmt_type, title, level, value, display_order = row
            
            # Group by period and statement type
            if period != current_period:
                click.echo(f"\n🗓️  {period}")
                current_period = period
                current_statement = None
            
            if stmt_type != current_statement:
                stmt_titles = {
                    'candoiketoan': 'Bảng cân đối kế toán',
                    'baocaothunhap': 'Báo cáo thu nhập', 
                    'luuchuyentiente': 'Báo cáo lưu chuyển tiền tệ',
                    'thuyetminh': 'Thuyết minh báo cáo tài chính'
                }
                click.echo(f"  📋 {stmt_titles.get(stmt_type, stmt_type)}")
                current_statement = stmt_type
            
            # Create indentation based on level (SQLite-compatible)
            indent = "  " * (level - 1) if level > 1 else ""
            indented_title = indent + title
            
            # Format value (assume values are in millions VND)
            if value is not None:
                formatted_value = f"{value:,.1f} (triệu VND)"
            else:
                formatted_value = "N/A"
            
            click.echo(f"    {indented_title}: {formatted_value}")
        
    except Exception as e:
        click.echo(f"❌ Query failed: {e}", err=True)
        sys.exit(1)
    finally:
        db.close()


@cli.command()
def list_companies():
    """List all companies in the database"""
    
    try:
        db = next(get_db())
        
        result = db.execute(text("""
        SELECT c.ticker, c.name, COUNT(DISTINCT iv.item_value_id) as value_count
        FROM company c
        LEFT JOIN item_value iv ON c.company_id = iv.company_id
        GROUP BY c.company_id, c.ticker, c.name
        ORDER BY c.ticker
        """))
        
        rows = result.fetchall()
        
        if not rows:
            click.echo("📭 No companies found in database")
            return
        
        click.echo("🏢 Companies in database:")
        click.echo("=" * 60)
        
        for ticker, name, value_count in rows:
            click.echo(f"{ticker:10} | {name:35} | {value_count:4} data points")
        
    except Exception as e:
        click.echo(f"❌ Failed to list companies: {e}", err=True)
        sys.exit(1)
    finally:
        db.close()


@cli.command()
@click.option('--sample-size', type=int, default=5, help='Number of sample items to show')
def validate_schema(sample_size):
    """Validate that the financial schema is properly set up"""
    
    try:
        db = next(get_db())
        
        # Test that all tables exist and basic queries work
        tables = ['company', 'period', 'statement', 'statement_item', 'item_value']
        
        click.echo("🔍 Validating financial schema...")
        
        for table in tables:
            try:
                result = db.execute(text(f"SELECT COUNT(*) FROM {table}"))
                count = result.fetchone()[0]
                click.echo(f"  ✅ {table}: {count} records")
            except Exception as e:
                click.echo(f"  ❌ {table}: {e}")
                return
        
        # Test views
        try:
            result = db.execute(text("SELECT COUNT(*) FROM v_financial_data"))
            count = result.fetchone()[0]
            click.echo(f"  ✅ v_financial_data view: {count} records")
        except Exception as e:
            click.echo(f"  ❌ v_financial_data view: {e}")
        
        # Note: v_statement_hierarchy view was not created for SQLite compatibility
        click.echo("  ℹ️  v_statement_hierarchy view skipped (not created for SQLite)")
        
        click.echo("\n✅ Schema validation completed!")
        
    except Exception as e:
        click.echo(f"❌ Schema validation failed: {e}", err=True)
        sys.exit(1)
    finally:
        db.close()


@cli.command()
def warm_cache():
    """Pre-warm the statement cache for better performance"""
    
    try:
        db = next(get_db())
        
        importer = FinancialDataImporter(db)
        cache_contents = importer.warm_statement_cache()
        
        click.echo("🔥 Statement cache warmed successfully!")
        click.echo("📋 Cached statements:")
        for stmt_type, stmt_id in cache_contents.items():
            click.echo(f"  - {stmt_type}: ID {stmt_id}")
        
    except Exception as e:
        click.echo(f"❌ Failed to warm cache: {e}", err=True)
        sys.exit(1)
    finally:
        db.close()


@cli.command()
def clear_cache():
    """Clear the statement cache"""
    
    try:
        FinancialDataImporter.clear_statement_cache()
        click.echo("🗑️  Statement cache cleared successfully!")
        
    except Exception as e:
        click.echo(f"❌ Failed to clear cache: {e}", err=True)
        sys.exit(1)


@cli.command()
def help_commands():
    """Show available commands with examples"""
    click.echo("📚 Financial Data CLI Commands:")
    click.echo("")
    click.echo("🏢 Company Management:")
    click.echo("  add-company VCG \"Vietcombank\"              # Add new company")
    click.echo("  list-companies                              # List all companies")
    click.echo("")
    click.echo("📊 Data Import:")
    click.echo("  import-data financial.json VCG --dry-run    # Validate JSON")
    click.echo("  import-data financial.json VCG              # Import data")
    click.echo("")
    click.echo("🔍 Data Query:")
    click.echo("  query-data VCG                              # Query all data")
    click.echo("  query-data VCG --period Q2-2025            # Query specific period")
    click.echo("  query-data VCG --statement-type candoiketoan # Query balance sheet")
    click.echo("  query-data VCG --max-level 2               # Limit hierarchy depth")
    click.echo("")
    click.echo("⚙️  System:")
    click.echo("  validate-schema                             # Check database setup")
    click.echo("  warm-cache                                  # Pre-warm statement cache")
    click.echo("  clear-cache                                 # Clear statement cache")


if __name__ == '__main__':
    cli()
