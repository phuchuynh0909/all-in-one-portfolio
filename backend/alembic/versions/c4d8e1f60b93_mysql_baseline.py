"""Squashed MySQL baseline.

The app ran on a single-file SQLite database (``portfolio.db``) up to revision
``a1b2c3d4e5f6``. It now runs on MySQL (``my_portfolio``), alongside the wichart
report store that already lived there.

The nine revisions before this one are *not* replayable on MySQL: twenty of
their columns are declared ``sa.String()`` with no length, and MySQL rejects a
``VARCHAR`` without one. Rather than rewrite that history, this revision is a
squashed baseline — it creates the whole schema in one step, with explicit
lengths, on an empty database.

To bring a fresh MySQL database up::

    alembic stamp a1b2c3d4e5f6   # skip the un-replayable SQLite-era history
    alembic upgrade head         # this revision builds everything

``upgrade()`` inspects before it creates, so it is also a no-op against an
existing SQLite ``portfolio.db`` (where every table is already present) and is
safe to re-run. Later migrations chain off this revision and run on both.

Lengths are chosen from the migrated data (longest ticker 7, longest company
name 19, longest sector name 34) with room to grow; ``item_key`` is capped at
255 so the ``(statement_id, item_key)`` unique index stays inside InnoDB's
3072-byte key limit at utf8mb4's 4 bytes per character.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4d8e1f60b93'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


# The view is recreated rather than migrated: it is a pure projection over the
# financial tables and its body is portable ANSI, valid on both backends.
V_FINANCIAL_DATA = """
CREATE VIEW v_financial_data AS
    SELECT
        c.company_id,
        c.ticker,
        c.name AS company_name,
        p.period_id,
        p.label AS period_label,
        p.period_type,
        s.statement_id,
        s.statement_type,
        s.title AS statement_title,
        si.item_id,
        si.item_key,
        si.title_vi,
        si.level,
        si.parent_item_id,
        si.display_order,
        iv.value
    FROM company c
    JOIN item_value iv ON c.company_id = iv.company_id
    JOIN period p ON iv.period_id = p.period_id
    JOIN statement_item si ON iv.item_id = si.item_id
    JOIN statement s ON si.statement_id = s.statement_id
"""

# Every sector metric column: same type, listed once instead of twenty-three
# near-identical sa.Column lines.
SECTOR_METRICS = (
    'smg', 'dif', 'dif_w', 'dif_m', 'dif_3m', 'vonhoa_d', 'eps_d', 'pe_d',
    'pb_d', 'roa_ttm', 'roe_ttm', 'lnst_yoy_ttm', 'doanhthuthuan_ttm',
    'lnst_ttm', 'ocf_ttm', 'lnst_yoy_q', 'novay_q', 'tonkho_q', 'phaithu_q',
    'tts_q', 'vcsh_q',
)


def _existing_tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _existing_tables()

    def create(name: str, *columns, **kw) -> bool:
        """Create the table unless it is already there. Returns True if created."""
        if name in existing:
            return False
        op.create_table(name, *columns, **kw)
        return True

    # ---- portfolio ------------------------------------------------------
    create(
        'positions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('ticker', sa.String(10), nullable=False),
        sa.Column('quantity', sa.DECIMAL(15, 6), nullable=False),
        sa.Column('purchase_price', sa.DECIMAL(15, 6), nullable=False),
        sa.Column('purchase_date', sa.Date(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )

    create(
        'transactions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('ticker', sa.String(10), nullable=False),
        sa.Column('transaction_type', sa.Enum('buy', 'sell'), nullable=False),
        sa.Column('quantity', sa.DECIMAL(15, 6), nullable=False),
        sa.Column('price', sa.DECIMAL(15, 6), nullable=False),
        sa.Column('close_price', sa.DECIMAL(15, 6), nullable=True),
        sa.Column('transaction_date', sa.Date(), nullable=False),
        sa.Column('fees', sa.DECIMAL(10, 2), server_default=sa.text('0')),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )

    create(
        'investment_amounts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('amount', sa.DECIMAL(15, 2), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )

    if create(
        'price_alerts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('symbol', sa.String(20), nullable=False),
        # ``condition`` is a MySQL reserved word; SQLAlchemy quotes it for us.
        sa.Column('condition', sa.Enum('gt', 'gte', 'lt', 'lte', 'eq', name='alert_condition'), nullable=False),
        sa.Column('target_price', sa.DECIMAL(15, 6), nullable=False),
        sa.Column('is_active', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column('is_triggered', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('triggered_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
    ):
        op.create_index('ix_price_alerts_symbol', 'price_alerts', ['symbol'])
        op.create_index('ix_price_alerts_is_active', 'price_alerts', ['is_active'])

    # ---- market ---------------------------------------------------------
    create(
        'sector',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column('level', sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column('type', sa.String(10), nullable=True),
        sa.Column('name', sa.String(255), nullable=True),
        *[sa.Column(m, sa.DECIMAL(20, 8), nullable=True) for m in SECTOR_METRICS],
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )

    create(
        'stock_symbol',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('symbol', sa.String(20), nullable=False),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('id_sector_level_1', sa.Integer(), nullable=True),
        sa.Column('id_sector_level_2', sa.Integer(), nullable=True),
        sa.Column('id_sector_level_3', sa.Integer(), nullable=True),
        sa.Column('id_sector_level_4', sa.Integer(), nullable=True),
        sa.Column('vonhoa_d', sa.DECIMAL(20, 8), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )

    # ---- financial ------------------------------------------------------
    if create(
        'company',
        sa.Column('company_id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('ticker', sa.String(20), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
    ):
        op.create_index('ix_company_ticker', 'company', ['ticker'], unique=True)

    if create(
        'period',
        sa.Column('period_id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('label', sa.String(32), nullable=False),
        sa.Column('period_type', sa.String(16), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.CheckConstraint(
            "period_type IN ('quarter','year','month','other')",
            name='period_type_check',
        ),
    ):
        op.create_index('ix_period_label', 'period', ['label'], unique=True)
        op.create_index('ix_period_period_type', 'period', ['period_type'])
        op.create_index('ix_period_end_date', 'period', ['end_date'])

    if create(
        'statement',
        sa.Column('statement_id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('statement_type', sa.String(32), nullable=False),
        sa.Column('title', sa.String(255), nullable=True),
        sa.CheckConstraint(
            "statement_type IN ('candoiketoan','baocaothunhap','luuchuyentiente','thuyetminh')",
            name='statement_type_check',
        ),
    ):
        op.create_index('ix_statement_statement_type', 'statement', ['statement_type'], unique=True)

    if create(
        'statement_item',
        sa.Column('item_id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('statement_id', sa.Integer(), nullable=False),
        sa.Column('item_key', sa.String(255), nullable=False),
        sa.Column('title_vi', sa.String(500), nullable=False),
        sa.Column('level', sa.Integer(), nullable=False),
        sa.Column('parent_item_id', sa.Integer(), nullable=True),
        sa.Column('display_order', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['statement_id'], ['statement.statement_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['parent_item_id'], ['statement_item.item_id']),
        sa.UniqueConstraint('statement_id', 'item_key'),
    ):
        op.create_index('ix_statement_item_statement_id', 'statement_item', ['statement_id'])
        op.create_index('ix_statement_item_item_key', 'statement_item', ['item_key'])
        op.create_index('ix_statement_item_level', 'statement_item', ['level'])
        op.create_index('ix_statement_item_parent_item_id', 'statement_item', ['parent_item_id'])
        op.create_index('ix_statement_item_level_order', 'statement_item', ['level', 'display_order'])

    if create(
        'item_value',
        sa.Column('item_value_id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('period_id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('value', sa.NUMERIC(20, 1), nullable=False),
        sa.ForeignKeyConstraint(['item_id'], ['statement_item.item_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['period_id'], ['period.period_id']),
        sa.ForeignKeyConstraint(['company_id'], ['company.company_id']),
        sa.UniqueConstraint('item_id', 'period_id', 'company_id'),
    ):
        op.create_index('ix_item_value_item_id', 'item_value', ['item_id'])
        op.create_index('ix_item_value_period_id', 'item_value', ['period_id'])
        op.create_index('ix_item_value_company_id', 'item_value', ['company_id'])
        op.create_index('ix_item_value_composite', 'item_value', ['item_id', 'period_id', 'company_id'])

    # ---- view -----------------------------------------------------------
    op.execute("DROP VIEW IF EXISTS v_financial_data")
    op.execute(V_FINANCIAL_DATA)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_financial_data")
    for name in (
        'item_value', 'statement_item', 'statement', 'period', 'company',
        'stock_symbol', 'sector', 'price_alerts', 'investment_amounts',
        'transactions', 'positions',
    ):
        op.drop_table(name)
