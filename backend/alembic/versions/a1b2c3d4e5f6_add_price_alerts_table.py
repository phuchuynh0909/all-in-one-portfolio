"""Add price_alerts table

Revision ID: a1b2c3d4e5f6
Revises: f7b8c2d1a9e3
Create Date: 2026-01-15 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '53fecbbe1252'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create price_alerts table for managing price-based alerts."""
    op.create_table('price_alerts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('symbol', sa.String(length=20), nullable=False),
        sa.Column('condition', sa.Enum('gt', 'gte', 'lt', 'lte', 'eq', name='alert_condition'), nullable=False),
        sa.Column('target_price', sa.DECIMAL(precision=15, scale=6), nullable=False),
        sa.Column('is_active', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column('is_triggered', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('triggered_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_price_alerts_symbol', 'price_alerts', ['symbol'])
    op.create_index('ix_price_alerts_is_active', 'price_alerts', ['is_active'])


def downgrade() -> None:
    """Drop price_alerts table."""
    op.drop_index('ix_price_alerts_is_active', table_name='price_alerts')
    op.drop_index('ix_price_alerts_symbol', table_name='price_alerts')
    op.drop_table('price_alerts')
    # Drop the enum type (for PostgreSQL)
    op.execute("DROP TYPE IF EXISTS alert_condition")

