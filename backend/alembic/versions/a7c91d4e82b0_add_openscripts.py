"""Add account-owned OpenScript sources.

Revision ID: a7c91d4e82b0
Revises: f3a92d47c1e8
"""
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGTEXT
from alembic import op

revision = "a7c91d4e82b0"
down_revision = "f3a92d47c1e8"
branch_labels = None
depends_on = None


def upgrade():
    source_type = sa.Text().with_variant(LONGTEXT(), "mysql")
    op.create_table(
        "openscripts",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("id", sa.String(72), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("source", source_type, nullable=False),
        sa.Column("applied_source", source_type, nullable=True),
        sa.Column("visible", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade():
    op.drop_table("openscripts")
