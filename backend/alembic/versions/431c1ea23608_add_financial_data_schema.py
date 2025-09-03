"""add_financial_data_schema

Revision ID: 431c1ea23608
Revises: ec6af361d293
Create Date: 2025-09-02 21:20:16.727895

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '431c1ea23608'
down_revision: Union[str, None] = 'ec6af361d293'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Company table
    op.create_table(
        'company',
        sa.Column('company_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('ticker', sa.String(), nullable=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('company_id'),
        sa.UniqueConstraint('ticker')
    )

    # Period table
    op.create_table(
        'period',
        sa.Column('period_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('label', sa.String(), nullable=False),
        sa.Column('period_type', sa.String(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint('period_id'),
        sa.UniqueConstraint('label'),
        sa.CheckConstraint("period_type IN ('quarter','year','month','other')", name='period_type_check')
    )

    # Report table
    op.create_table(
        'report',
        sa.Column('report_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('as_of_period_id', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(), nullable=True),
        sa.Column('currency', sa.String(), nullable=True, default='VND'),
        sa.Column('unit_scale', sa.Integer(), nullable=True, default=1),
        sa.Column('type', sa.Integer(), nullable=True),
        sa.Column('total_items', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('report_id'),
        sa.ForeignKeyConstraint(['company_id'], ['company.company_id']),
        sa.ForeignKeyConstraint(['as_of_period_id'], ['period.period_id'])
    )

    # Statement table
    op.create_table(
        'statement',
        sa.Column('statement_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('report_id', sa.Integer(), nullable=False),
        sa.Column('statement_type', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('statement_id'),
        sa.ForeignKeyConstraint(['report_id'], ['report.report_id'], ondelete='CASCADE'),
        sa.UniqueConstraint('report_id', 'statement_type'),
        sa.CheckConstraint("statement_type IN ('candoiketoan','baocaothunhap','luuchuyentiente','thuyetminh')", 
                          name='statement_type_check')
    )

    # Statement Item table
    op.create_table(
        'statement_item',
        sa.Column('item_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('statement_id', sa.Integer(), nullable=False),
        sa.Column('item_key', sa.String(), nullable=False),
        sa.Column('title_vi', sa.String(), nullable=False),
        sa.Column('level', sa.Integer(), nullable=False),
        sa.Column('parent_item_id', sa.Integer(), nullable=True),
        sa.Column('display_order', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('item_id'),
        sa.ForeignKeyConstraint(['statement_id'], ['statement.statement_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['parent_item_id'], ['statement_item.item_id']),
        sa.UniqueConstraint('statement_id', 'item_key')
    )

    # Item Value table
    op.create_table(
        'item_value',
        sa.Column('item_value_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('period_id', sa.Integer(), nullable=False),
        sa.Column('value', sa.NUMERIC(precision=20, scale=1), nullable=False),
        sa.PrimaryKeyConstraint('item_value_id'),
        sa.ForeignKeyConstraint(['item_id'], ['statement_item.item_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['period_id'], ['period.period_id']),
        sa.UniqueConstraint('item_id', 'period_id')
    )

    # Create indexes
    op.create_index('ix_statement_item_parent', 'statement_item', ['parent_item_id'])
    op.create_index('ix_item_value_item', 'item_value', ['item_id'])
    op.create_index('ix_item_value_period', 'item_value', ['period_id'])
    op.create_index('ix_statement_item_level', 'statement_item', ['level'])
    op.create_index('ix_statement_item_key', 'statement_item', ['item_key'])
    op.create_index('ix_report_company', 'report', ['company_id'])
    op.create_index('ix_statement_type', 'statement', ['statement_type'])

    # Create SQLite-compatible views
    op.execute("""
        CREATE VIEW v_financial_data AS
        SELECT 
          c.ticker,
          c.name as company_name,
          r.report_id,
          s.statement_type,
          si.item_key,
          si.title_vi,
          si.level,
          p.label as period_label,
          p.period_type,
          p.start_date,
          p.end_date,
          iv.value,
          r.currency,
          r.unit_scale
        FROM company c
        JOIN report r ON c.company_id = r.company_id
        JOIN statement s ON r.report_id = s.report_id
        JOIN statement_item si ON s.statement_id = si.statement_id
        JOIN item_value iv ON si.item_id = iv.item_id
        JOIN period p ON iv.period_id = p.period_id;
    """)

    # Note: SQLite doesn't support COMMENT ON statements
    # Table and column comments are documented in the SQLAlchemy models instead


def downgrade() -> None:
    # Drop views first
    op.execute("DROP VIEW IF EXISTS v_financial_data")
    
    # Drop indexes (they'll be dropped with tables anyway, but being explicit)
    op.drop_index('ix_statement_type')
    op.drop_index('ix_report_company')
    op.drop_index('ix_statement_item_key')
    op.drop_index('ix_statement_item_level')
    op.drop_index('ix_item_value_period')
    op.drop_index('ix_item_value_item')
    op.drop_index('ix_statement_item_parent')
    
    # Drop tables in reverse dependency order
    op.drop_table('item_value')
    op.drop_table('statement_item')
    op.drop_table('statement')
    op.drop_table('report')
    op.drop_table('period')
    op.drop_table('company')
