"""Protect position-close transactions from independent edits or deletion."""
from alembic import op
import sqlalchemy as sa

revision = 'f1c90b8a42d3'
down_revision = 'ed24f8c93a10'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('transactions', sa.Column('is_position_close', sa.Boolean(), nullable=False, server_default=sa.false()))
    # Old sell rows do not say whether they changed a position, so protect them.
    op.execute("UPDATE transactions SET is_position_close = 1 WHERE transaction_type = 'sell'")


def downgrade():
    op.drop_column('transactions', 'is_position_close')
