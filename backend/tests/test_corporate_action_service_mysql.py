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


from unittest.mock import Mock

from app.services import corporate_action_service as cas
from app.services.dnse_corporate_actions import RawEvent
from app.schemas.corporate_action import ManualDividendCreate
from app.schemas.portfolio import PositionCreate
from app.services import portfolio_service


def _raw(event_id, name, title, ex, symbol="TST"):
    return RawEvent(symbol=symbol, event_id=event_id, name=name, title=title,
                    ex_date=date.fromisoformat(ex), record_date=None,
                    pay_date=None, url=None)


def _fake_fetch(events):
    return Mock(side_effect=lambda symbol, **kw: [e for e in events if e.symbol == symbol])


def test_held_symbols_are_distinct_and_upper(db):
    db.execute(text("DELETE FROM positions"))
    for tk in ("aaa", "AAA", "bbb"):
        portfolio_service.create_position(db, PositionCreate(
            ticker=tk, quantity=Decimal("10"), purchase_price=Decimal("10"),
            purchase_date=date(2025, 1, 1)))

    assert cas.held_symbols(db) == ["AAA", "BBB"]


def test_sync_inserts_parsed_events_and_flags_the_rest(db, monkeypatch):
    events = [
        _raw(999100001, "Trả cổ tức bằng tiền mặt",
             "Trả cổ tức năm 2025 bằng tiền 800 đồng/CP", "2026-01-10"),
        _raw(999100002, "Thưởng cổ phiếu", "Thưởng cổ phiếu tỷ lệ 100:10", "2026-02-10"),
        _raw(999100003, "Thưởng cổ phiếu", "Thưởng cổ phiếu nothing here", "2026-03-10"),
        _raw(999100004, "Họp ĐHCĐ bất thường", "Họp ĐHCĐ bất thường 2026", "2026-04-10"),
    ]
    monkeypatch.setattr(cas, "fetch_history", _fake_fetch(events))

    counts = cas.sync_symbol(db, "TST")

    assert counts == {"inserted": 3, "skipped": 0, "unparsed": 1, "ignored": 1}
    rows = {r.event_id: r for r in cas.list_actions(db, symbol="TST", status=None)}
    assert rows[999100001].status == "pending"
    assert rows[999100001].amount_per_share == Decimal("800.000000")
    assert rows[999100001].tax_withheld_pct == Decimal("0.0500")
    assert rows[999100002].ratio == Decimal("0.10000000")
    assert rows[999100002].tax_withheld_pct is None
    assert rows[999100003].status == "unparsed"
    assert rows[999100003].title == "Thưởng cổ phiếu nothing here"
    assert 999100004 not in rows  # meetings are never stored


def test_sync_is_idempotent(db, monkeypatch):
    events = [_raw(999100010, "Thưởng cổ phiếu", "Thưởng cổ phiếu tỷ lệ 100:10",
                   "2026-02-10")]
    monkeypatch.setattr(cas, "fetch_history", _fake_fetch(events))

    first = cas.sync_symbol(db, "TST")
    second = cas.sync_symbol(db, "TST")

    assert first["inserted"] == 1
    assert second == {"inserted": 0, "skipped": 1, "unparsed": 0, "ignored": 0}
    assert len(cas.list_actions(db, symbol="TST", status=None)) == 1


def test_list_actions_defaults_to_pending(db, monkeypatch):
    events = [
        _raw(999100020, "Thưởng cổ phiếu", "Thưởng cổ phiếu tỷ lệ 100:10", "2026-02-10"),
        _raw(999100021, "Thưởng cổ phiếu", "Thưởng cổ phiếu broken", "2026-02-11"),
    ]
    monkeypatch.setattr(cas, "fetch_history", _fake_fetch(events))
    cas.sync_symbol(db, "TST")

    pending = cas.list_actions(db, symbol="TST")
    assert [r.event_id for r in pending] == [999100020]


def test_manual_cash_dividend_is_stored_pending(db):
    ca = cas.create_manual(db, ManualDividendCreate(
        symbol="tst", action_type="cash", ex_date=date(2026, 5, 1),
        amount_per_share=Decimal("1200"), notes="from broker statement"))

    assert ca.status == "pending"
    assert ca.source == "manual"
    assert ca.symbol == "TST"
    assert ca.amount_per_share == Decimal("1200.000000")
    assert ca.tax_withheld_pct == Decimal("0.0500")
    assert ca.event_id < 0  # synthetic, cannot collide with a DNSE id


def test_manual_stock_dividend_requires_a_ratio(db):
    with pytest.raises(ValueError, match="ratio"):
        cas.create_manual(db, ManualDividendCreate(
            symbol="TST", action_type="stock", ex_date=date(2026, 5, 1)))


def test_manual_cash_dividend_requires_an_amount(db):
    with pytest.raises(ValueError, match="amount_per_share"):
        cas.create_manual(db, ManualDividendCreate(
            symbol="TST", action_type="cash", ex_date=date(2026, 5, 1)))


def test_create_manual_retries_past_an_event_id_collision(db, monkeypatch):
    """Two racing manual inserts can mint the same negative id.

    Rather than simulate real concurrency, force the first commit attempt to
    raise the ``IntegrityError`` a colliding insert would produce, and let the
    second attempt go through for real.
    """
    calls = {"n": 0}
    real_commit = db.commit

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("INSERT", {}, Exception("duplicate event_id"))
        return real_commit()

    monkeypatch.setattr(db, "commit", flaky_commit)

    ca = cas.create_manual(db, ManualDividendCreate(
        symbol="TST", action_type="cash", ex_date=date(2026, 5, 1),
        amount_per_share=Decimal("500")))

    assert calls["n"] == 2  # first attempt collided, second succeeded
    assert ca.status == "pending"
    assert ca.event_id < 0  # the negative-id scheme still holds after a retry
