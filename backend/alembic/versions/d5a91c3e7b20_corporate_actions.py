"""corporate action archive, application ledger, dividend transaction types

Revision ID: d5a91c3e7b20
Revises: c4d8e1f60b93
"""
from alembic import op
import sqlalchemy as sa


revision = 'd5a91c3e7b20'
down_revision = 'c4d8e1f60b93'
branch_labels = None
depends_on = None

# Widening only: existing 'buy'/'sell' rows are untouched by either direction.
_OLD_TYPES = sa.Enum('buy', 'sell', name='transaction_type')
_NEW_TYPES = sa.Enum('buy', 'sell', 'dividend_cash', 'dividend_stock',
                     name='transaction_type')


def upgrade() -> None:
    op.create_table(
        'corporate_action',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('symbol', sa.String(20), nullable=False),
        sa.Column('event_id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('action_type', sa.Enum('cash', 'stock', name='ca_action_type'),
                  nullable=False),
        sa.Column('ex_date', sa.Date(), nullable=False),
        sa.Column('record_date', sa.Date(), nullable=True),
        sa.Column('pay_date', sa.Date(), nullable=True),
        sa.Column('amount_per_share', sa.DECIMAL(15, 6), nullable=True),
        sa.Column('ratio', sa.DECIMAL(15, 8), nullable=True),
        sa.Column('tax_withheld_pct', sa.DECIMAL(5, 4), nullable=True),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('url', sa.String(1024), nullable=True),
        sa.Column('source', sa.Enum('dnse_history', 'dnse_calendar', 'manual',
                                    name='ca_source'), nullable=False),
        sa.Column('status', sa.Enum('pending', 'applied', 'ignored', 'unparsed',
                                    name='ca_status'), nullable=False,
                  server_default=sa.text("'pending'")),
        sa.Column('applied_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(),
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.TIMESTAMP(),
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('event_id', name='uq_corporate_action_event_id'),
    )
    op.create_index('ix_corporate_action_symbol', 'corporate_action', ['symbol'])
    op.create_index('ix_corporate_action_ex_date', 'corporate_action', ['ex_date'])

    op.create_table(
        'corporate_action_application',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('corporate_action_id', sa.Integer(), nullable=False),
        sa.Column('position_id', sa.Integer(), nullable=True),
        sa.Column('transaction_id', sa.Integer(), nullable=True),
        sa.Column('qty_before', sa.DECIMAL(15, 6), nullable=False),
        sa.Column('qty_after', sa.DECIMAL(15, 6), nullable=False),
        sa.Column('price_before', sa.DECIMAL(15, 6), nullable=False),
        sa.Column('price_after', sa.DECIMAL(15, 6), nullable=False),
        sa.Column('cash_amount', sa.DECIMAL(20, 6), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(),
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['corporate_action_id'], ['corporate_action.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id']),
        sa.UniqueConstraint('corporate_action_id', 'position_id',
                            name='uq_ca_application_action_position'),
    )
    op.create_index('ix_ca_application_corporate_action_id',
                    'corporate_action_application', ['corporate_action_id'])
    op.create_index('ix_ca_application_position_id',
                    'corporate_action_application', ['position_id'])

    op.alter_column('transactions', 'transaction_type',
                    existing_type=_OLD_TYPES, type_=_NEW_TYPES, nullable=False)


def downgrade() -> None:
    # Dividend rows must go before the type can narrow, or the ALTER truncates
    # them to an empty string.
    op.execute("DELETE FROM transactions "
               "WHERE transaction_type IN ('dividend_cash', 'dividend_stock')")
    op.alter_column('transactions', 'transaction_type',
                    existing_type=_NEW_TYPES, type_=_OLD_TYPES, nullable=False)
    op.drop_table('corporate_action_application')
    op.drop_index('ix_corporate_action_ex_date', table_name='corporate_action')
    op.drop_index('ix_corporate_action_symbol', table_name='corporate_action')
    op.drop_table('corporate_action')
