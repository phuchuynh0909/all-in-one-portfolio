"""Add a durable queue for report RAG submissions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = 'a27f6d13b408'
down_revision = 'f1c90b8a42d3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'rag_submissions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('report_id', sa.BigInteger(), nullable=False),
        sa.Column('active_report_id', sa.BigInteger(), nullable=True, unique=True),
        sa.Column('recreate', sa.Boolean(), nullable=False),
        sa.Column('parser', sa.String(32), nullable=True),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('flow_run_id', sa.String(36), nullable=True),
        sa.Column('created_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql'), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
    )
    op.create_index('ix_rag_submissions_report_id', 'rag_submissions', ['report_id'])
    op.create_index('ix_rag_submissions_state', 'rag_submissions', ['state'])


def downgrade():
    op.drop_table('rag_submissions')
