"""Persist account-owned chart layouts and selected layout in MySQL."""
from alembic import op
import sqlalchemy as sa

revision = "b8d02e5f93c1"
down_revision = "a7c91d4e82b0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "chart_layouts",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("id", sa.String(72), primary_key=True),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("indicators", sa.JSON(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("scripts", sa.JSON(), nullable=False),
        sa.Column("chart_state", sa.JSON(), nullable=True),
    )
    op.create_table(
        "chart_preferences",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("active_layout_id", sa.String(72), nullable=True),
    )


def downgrade():
    op.drop_table("chart_preferences")
    op.drop_table("chart_layouts")
