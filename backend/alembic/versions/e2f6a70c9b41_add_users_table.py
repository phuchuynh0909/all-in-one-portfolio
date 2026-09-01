"""add users table

Revision ID: e2f6a70c9b41
Revises: b7e2f4c81d35
Create Date: 2026-09-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2f6a70c9b41"
down_revision: Union[str, None] = "b7e2f4c81d35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")
