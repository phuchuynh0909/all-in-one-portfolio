"""add_sector_level_1_2_into_stock_symbol

Revision ID: 9d471c5cfaba
Revises: 4558b47687d2
Create Date: 2025-09-19 12:56:34.224873

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d471c5cfaba'
down_revision: Union[str, None] = '4558b47687d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns to stock_symbol table
    op.add_column('stock_symbol', sa.Column('id_sector_level_1', sa.Integer(), nullable=True))
    op.add_column('stock_symbol', sa.Column('id_sector_level_2', sa.Integer(), nullable=True))


def downgrade() -> None:
    # Remove the added columns
    op.drop_column('stock_symbol', 'id_sector_level_2')
    op.drop_column('stock_symbol', 'id_sector_level_1')

