"""Schema-level checks for the corporate-action tables on real MySQL."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.base import engine
from app.db.models.corporate_action import CorporateAction, CorporateActionApplication
from app.db.models.portfolio import Transaction
from tests.conftest import requires_mysql

pytestmark = requires_mysql


def test_tables_exist_with_expected_columns():
    cols = {t: {c["name"] for c in inspect(engine).get_columns(t)}
            for t in ("corporate_action", "corporate_action_application")}

    assert {"id", "symbol", "event_id", "name", "action_type", "ex_date",
            "record_date", "pay_date", "amount_per_share", "ratio",
            "tax_withheld_pct", "title", "url", "source", "status",
            "applied_at", "created_at", "updated_at"} <= cols["corporate_action"]
    assert {"id", "corporate_action_id", "position_id", "transaction_id",
            "qty_before", "qty_after", "price_before", "price_after",
            "cash_amount", "created_at"} <= cols["corporate_action_application"]


def test_event_id_is_unique(db):
    def add(event_id):
        db.add(CorporateAction(
            symbol="TST", event_id=event_id, name="Thưởng cổ phiếu",
            action_type="stock", ex_date=date(2026, 1, 1),
            ratio=Decimal("0.1"), title="Thưởng cổ phiếu tỷ lệ 100:10",
            source="dnse_history", status="pending",
        ))
        db.flush()

    add(999000001)
    with pytest.raises(IntegrityError):
        add(999000001)


def test_application_position_pair_is_unique(db):
    """One event applied to one lot twice must be rejected.

    Idempotency depends on this: a later re-run of the same corporate action
    against the same position must not be allowed to double-apply.
    """
    ca = CorporateAction(
        symbol="TST", event_id=999000003, name="Thưởng cổ phiếu",
        action_type="stock", ex_date=date(2026, 1, 1),
        ratio=Decimal("0.1"), title="Thưởng cổ phiếu tỷ lệ 100:10",
        source="dnse_history", status="pending",
    )
    db.add(ca)
    db.flush()

    def add_application():
        db.add(CorporateActionApplication(
            corporate_action_id=ca.id, position_id=42,
            qty_before=Decimal("100"), qty_after=Decimal("110"),
            price_before=Decimal("20"), price_after=Decimal("18.18"),
        ))
        db.flush()

    add_application()
    with pytest.raises(IntegrityError):
        add_application()


def test_vietnamese_title_round_trips(db):
    title = "Trả cổ tức năm 2025 bằng cổ phiếu tỷ lệ 100:8"
    ca = CorporateAction(
        symbol="VCG", event_id=999000002, name="Trả cổ tức bằng cổ phiếu",
        action_type="stock", ex_date=date(2026, 7, 14), ratio=Decimal("0.08"),
        title=title, source="dnse_history", status="pending",
    )
    db.add(ca)
    db.flush()
    db.expire(ca)
    assert ca.title == title


@pytest.mark.parametrize("kind", ["dividend_cash", "dividend_stock"])
def test_transactions_accepts_the_new_enum_values(db, kind):
    t = Transaction(
        ticker="TST", transaction_type=kind, quantity=Decimal("10"),
        price=Decimal("0") if kind == "dividend_stock" else Decimal("800"),
        transaction_date=date(2026, 1, 1),
    )
    db.add(t)
    db.flush()
    assert db.execute(
        text("SELECT transaction_type FROM transactions WHERE id = :i"), {"i": t.id}
    ).scalar() == kind


def test_transaction_schema_allows_dividend_rows():
    """The ENUM migration is useless while Pydantic rejects the same values."""
    from app.schemas.portfolio import TransactionCreate

    stock = TransactionCreate(
        ticker="TST", transaction_type="dividend_stock", quantity=Decimal("10"),
        price=Decimal("0"), transaction_date=date(2026, 1, 1),
    )
    assert stock.price == Decimal("0")

    cash = TransactionCreate(
        ticker="TST", transaction_type="dividend_cash", quantity=Decimal("1000"),
        price=Decimal("800"), transaction_date=date(2026, 1, 1),
    )
    assert cash.transaction_type == "dividend_cash"
