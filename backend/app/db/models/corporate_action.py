"""Corporate action archive and application ledger.

``corporate_action`` is both what DNSE told us and what we decided to do about
it. ``title`` is stored verbatim because it is the only evidence of what the
parser read — the adjudicating case being TRC's ``tỷ lệ 01:03``, where zero
padding leaves the intended ratio genuinely ambiguous.

``corporate_action_application`` records the before/after of every lot an event
touched, which is the only thing that makes the mutation reversible.
"""
from sqlalchemy import (
    DECIMAL, TIMESTAMP, BigInteger, Column, Date, Enum, ForeignKey, Index, Integer,
    String, UniqueConstraint, text,
)

from app.db.base import Base


class CorporateAction(Base):
    __tablename__ = "corporate_action"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    # DNSE's own id: the idempotency key that makes re-polling free.
    event_id = Column(BigInteger, nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    action_type = Column(Enum("cash", "stock", name="ca_action_type"), nullable=False)
    ex_date = Column(Date, nullable=False, index=True)
    record_date = Column(Date, nullable=True)
    pay_date = Column(Date, nullable=True)
    amount_per_share = Column(DECIMAL(15, 6), nullable=True)  # cash, gross VND
    ratio = Column(DECIMAL(15, 8), nullable=True)             # stock, new/held
    tax_withheld_pct = Column(DECIMAL(5, 4), nullable=True)   # 0.05 for cash
    title = Column(String(500), nullable=False)
    url = Column(String(1024), nullable=True)
    source = Column(
        Enum("dnse_history", "dnse_calendar", "manual", name="ca_source"),
        nullable=False,
    )
    status = Column(
        Enum("pending", "applied", "ignored", "unparsed", name="ca_status"),
        nullable=False,
        server_default=text("'pending'"),
    )
    applied_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))


class CorporateActionApplication(Base):
    __tablename__ = "corporate_action_application"

    id = Column(Integer, primary_key=True, autoincrement=True)
    corporate_action_id = Column(
        Integer, ForeignKey("corporate_action.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Nullable: the lot may be closed and deleted long after this was applied.
    position_id = Column(Integer, nullable=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    qty_before = Column(DECIMAL(15, 6), nullable=False)
    qty_after = Column(DECIMAL(15, 6), nullable=False)
    price_before = Column(DECIMAL(15, 6), nullable=False)
    price_after = Column(DECIMAL(15, 6), nullable=False)
    cash_amount = Column(DECIMAL(20, 6), nullable=True)  # gross, cash only
    created_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        # One event can hit one lot exactly once.
        UniqueConstraint("corporate_action_id", "position_id",
                         name="uq_ca_application_action_position"),
        # Names match the migration's explicit ``op.create_index`` calls —
        # SQLAlchemy's ``index=True`` default naming convention would drift.
        Index("ix_ca_application_corporate_action_id", "corporate_action_id"),
        Index("ix_ca_application_position_id", "position_id"),
    )
