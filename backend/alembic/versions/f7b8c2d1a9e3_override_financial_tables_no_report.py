"""override financial tables no report

Revision ID: f7b8c2d1a9e3
Revises: dc46dd7e53a4
Create Date: 2025-01-21 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f7b8c2d1a9e3'
down_revision = 'dc46dd7e53a4'
branch_labels = None
depends_on = None


def upgrade():
    """Override existing tables with simplified structure (no report table)"""
    
    # 1. Drop existing view
    op.execute("DROP VIEW IF EXISTS v_financial_data")
    
    # 2. Drop all financial tables in dependency order
    op.drop_table('item_value')
    op.drop_table('statement_item') 
    op.drop_table('statement')
    op.drop_table('report')
    
    # 3. Recreate statement table without report_id (master data)
    op.create_table('statement',
        sa.Column('statement_id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('statement_type', sa.String(), nullable=False, index=True),
        sa.Column('title', sa.String(), nullable=True),
        sa.UniqueConstraint('statement_type'),
        sa.CheckConstraint(
            "statement_type IN ('candoiketoan','baocaothunhap','luuchuyentiente','thuyetminh')", 
            name='statement_type_check'
        ),
    )
    
    # 4. Recreate statement_item table (master data)
    op.create_table('statement_item',
        sa.Column('item_id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('statement_id', sa.Integer(), nullable=False, index=True),
        sa.Column('item_key', sa.String(), nullable=False, index=True),
        sa.Column('title_vi', sa.String(), nullable=False),
        sa.Column('level', sa.Integer(), nullable=False, index=True),
        sa.Column('parent_item_id', sa.Integer(), nullable=True, index=True),
        sa.Column('display_order', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['statement_id'], ['statement.statement_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['parent_item_id'], ['statement_item.item_id']),
        sa.UniqueConstraint('statement_id', 'item_key'),
    )
    
    # 5. Recreate item_value table with company_id
    op.create_table('item_value',
        sa.Column('item_value_id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('item_id', sa.Integer(), nullable=False, index=True),
        sa.Column('period_id', sa.Integer(), nullable=False, index=True),
        sa.Column('company_id', sa.Integer(), nullable=False, index=True),
        sa.Column('value', sa.NUMERIC(precision=20, scale=1), nullable=False),
        sa.ForeignKeyConstraint(['item_id'], ['statement_item.item_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['period_id'], ['period.period_id']),
        sa.ForeignKeyConstraint(['company_id'], ['company.company_id']),
        sa.UniqueConstraint('item_id', 'period_id', 'company_id'),
    )
    
    # 6. Create indexes for performance
    op.create_index('ix_item_value_composite', 'item_value', ['item_id', 'period_id', 'company_id'])
    op.create_index('ix_statement_item_level_order', 'statement_item', ['level', 'display_order'])
    
    # 7. Create the new view
    op.execute("""
        CREATE VIEW v_financial_data AS
        SELECT 
            c.company_id,
            c.ticker,
            c.name as company_name,
            p.period_id,
            p.label as period_label,
            p.period_type,
            s.statement_id,
            s.statement_type,
            s.title as statement_title,
            si.item_id,
            si.item_key,
            si.title_vi,
            si.level,
            si.parent_item_id,
            si.display_order,
            iv.value
        FROM company c
        JOIN item_value iv ON c.company_id = iv.company_id
        JOIN period p ON iv.period_id = p.period_id
        JOIN statement_item si ON iv.item_id = si.item_id
        JOIN statement s ON si.statement_id = s.statement_id
    """)


def downgrade():
    """Restore original table structure with report table"""
    
    # 1. Drop the simplified view
    op.execute("DROP VIEW IF EXISTS v_financial_data")
    
    # 2. Drop simplified tables
    op.drop_table('item_value')
    op.drop_table('statement_item')
    op.drop_table('statement')
    
    # 3. Recreate report table
    op.create_table('report',
        sa.Column('report_id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('company_id', sa.Integer(), nullable=False, index=True),
        sa.Column('as_of_period_id', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(), nullable=True),
        sa.Column('currency', sa.String(), nullable=True),
        sa.Column('unit_scale', sa.Integer(), nullable=True),
        sa.Column('type', sa.Integer(), nullable=True),
        sa.Column('total_items', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['company_id'], ['company.company_id']),
        sa.ForeignKeyConstraint(['as_of_period_id'], ['period.period_id']),
    )
    op.create_index('ix_report_company_id', 'report', ['company_id'])
    op.create_index('ix_report_created_at', 'report', ['created_at'])
    
    # 4. Recreate statement table with report_id
    op.create_table('statement',
        sa.Column('statement_id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('report_id', sa.Integer(), nullable=False, index=True),
        sa.Column('statement_type', sa.String(), nullable=False, index=True),
        sa.Column('title', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['report_id'], ['report.report_id'], ondelete='CASCADE'),
        sa.UniqueConstraint('report_id', 'statement_type'),
        sa.CheckConstraint(
            "statement_type IN ('candoiketoan','baocaothunhap','luuchuyentiente','thuyetminh')", 
            name='statement_type_check'
        ),
    )
    
    # 5. Recreate statement_item table
    op.create_table('statement_item',
        sa.Column('item_id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('statement_id', sa.Integer(), nullable=False, index=True),
        sa.Column('item_key', sa.String(), nullable=False, index=True),
        sa.Column('title_vi', sa.String(), nullable=False),
        sa.Column('level', sa.Integer(), nullable=False, index=True),
        sa.Column('parent_item_id', sa.Integer(), nullable=True, index=True),
        sa.Column('display_order', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['statement_id'], ['statement.statement_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['parent_item_id'], ['statement_item.item_id']),
        sa.UniqueConstraint('statement_id', 'item_key'),
    )
    
    # 6. Recreate item_value table without company_id
    op.create_table('item_value',
        sa.Column('item_value_id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('item_id', sa.Integer(), nullable=False, index=True),
        sa.Column('period_id', sa.Integer(), nullable=False, index=True),
        sa.Column('value', sa.NUMERIC(precision=20, scale=1), nullable=False),
        sa.ForeignKeyConstraint(['item_id'], ['statement_item.item_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['period_id'], ['period.period_id']),
        sa.UniqueConstraint('item_id', 'period_id'),
    )
    
    # 7. Create original indexes
    op.create_index('ix_item_value_composite', 'item_value', ['item_id', 'period_id'])
    op.create_index('ix_statement_item_level_order', 'statement_item', ['level', 'display_order'])
    
    # 8. Recreate original view
    op.execute("""
        CREATE VIEW v_financial_data AS
        SELECT 
            c.company_id,
            c.ticker,
            c.name as company_name,
            r.report_id,
            r.source,
            r.currency,
            r.unit_scale,
            p.period_id,
            p.label as period_label,
            p.period_type,
            s.statement_id,
            s.statement_type,
            s.title as statement_title,
            si.item_id,
            si.item_key,
            si.title_vi,
            si.level,
            si.parent_item_id,
            si.display_order,
            iv.value
        FROM company c
        JOIN report r ON c.company_id = r.company_id
        LEFT JOIN period p ON r.as_of_period_id = p.period_id
        JOIN statement s ON r.report_id = s.report_id
        JOIN statement_item si ON s.statement_id = si.statement_id
        JOIN item_value iv ON si.item_id = iv.item_id AND iv.period_id = p.period_id
    """)
