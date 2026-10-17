"""Persist account-owned watchlists in MySQL."""
from alembic import op
import sqlalchemy as sa

revision = "c9e13f60a4d2"
down_revision = "b8d02e5f93c1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "watchlist_libraries",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("lists", sa.JSON(), nullable=False),
        sa.Column("active_list_id", sa.String(72), nullable=False),
        sa.Column("imported_ids", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("watchlist_libraries")
