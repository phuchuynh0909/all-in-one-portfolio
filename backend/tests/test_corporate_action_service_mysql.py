"""Schema-level checks for the corporate-action tables on real MySQL."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, inspect, select, text
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


from app.db.models.corporate_action import CorporateAction, CorporateActionApplication


def _pending(db, **kw):
    defaults = dict(
        symbol="TST", event_id=_pending.counter, name="Thưởng cổ phiếu",
        action_type="stock", ex_date=date(2026, 6, 1), ratio=Decimal("0.1"),
        title="Thưởng cổ phiếu tỷ lệ 100:10", source="dnse_history",
        status="pending",
    )
    _pending.counter += 1
    defaults.update(kw)
    ca = CorporateAction(**defaults)
    db.add(ca)
    db.flush()
    return ca


_pending.counter = 999200001


def _lot(db, ticker="TST", qty="1000", price="20", bought="2025-01-01"):
    return portfolio_service.create_position(db, PositionCreate(
        ticker=ticker, quantity=Decimal(qty), purchase_price=Decimal(price),
        purchase_date=date.fromisoformat(bought)))


def test_apply_stock_dividend_mutates_the_lot_and_books_a_transaction(db):
    db.execute(text("DELETE FROM positions"))
    lot = _lot(db, qty="10900", price="15.46", bought="2026-03-02")
    ca = _pending(db, ex_date=date(2026, 6, 17))

    result = cas.apply_action(db, ca.id)

    assert result.status == "applied"
    assert result.total_shares_added == Decimal("1090")
    refreshed = portfolio_service.get_position(db, lot.id)
    assert refreshed.quantity == Decimal("11990.000000")
    assert refreshed.purchase_price == Decimal("14.054545")

    tx = db.execute(text(
        "SELECT transaction_type, quantity, price FROM transactions WHERE id = :i"
    ), {"i": result.lots[0].transaction_id}).one()
    assert tx.transaction_type == "dividend_stock"
    assert tx.quantity == Decimal("1090.000000")
    assert tx.price == Decimal("0.000000")


def test_apply_cash_dividend_leaves_the_lot_alone(db):
    db.execute(text("DELETE FROM positions"))
    lot = _lot(db, qty="1000", price="20")
    ca = _pending(db, name="Trả cổ tức bằng tiền mặt", action_type="cash",
                  ratio=None, amount_per_share=Decimal("800"),
                  tax_withheld_pct=Decimal("0.05"),
                  title="Trả cổ tức năm 2025 bằng tiền 800 đồng/CP")

    result = cas.apply_action(db, ca.id)

    assert result.total_cash_gross == Decimal("800000")
    assert result.total_shares_added == Decimal("0")
    refreshed = portfolio_service.get_position(db, lot.id)
    assert refreshed.quantity == Decimal("1000.000000")
    assert refreshed.purchase_price == Decimal("20.000000")


def test_apply_skips_lots_bought_after_the_ex_date(db):
    db.execute(text("DELETE FROM positions"))
    _lot(db, qty="1000", price="20", bought="2026-01-01")
    _lot(db, qty="500", price="21", bought="2026-12-01")
    ca = _pending(db, ex_date=date(2026, 6, 1))

    result = cas.apply_action(db, ca.id)

    assert len(result.lots) == 1
    assert result.total_shares_added == Decimal("100")


def test_apply_twice_is_rejected(db):
    db.execute(text("DELETE FROM positions"))
    _lot(db)
    ca = _pending(db)
    cas.apply_action(db, ca.id)

    with pytest.raises(ValueError, match="already applied"):
        cas.apply_action(db, ca.id)


def test_apply_an_unparsed_event_is_rejected(db):
    ca = _pending(db, status="unparsed", ratio=None)
    with pytest.raises(ValueError, match="unparsed"):
        cas.apply_action(db, ca.id)


def test_apply_with_no_eligible_lot_still_marks_applied(db):
    db.execute(text("DELETE FROM positions"))
    ca = _pending(db)

    result = cas.apply_action(db, ca.id)

    assert result.lots == []
    assert result.status == "applied"


def test_unapply_restores_quantity_and_price_and_removes_the_transaction(db):
    db.execute(text("DELETE FROM positions"))
    lot = _lot(db, qty="10900", price="15.46", bought="2026-03-02")
    ca = _pending(db, ex_date=date(2026, 6, 17))
    applied = cas.apply_action(db, ca.id)
    tx_id = applied.lots[0].transaction_id

    cas.unapply_action(db, ca.id)

    refreshed = portfolio_service.get_position(db, lot.id)
    assert refreshed.quantity == Decimal("10900.000000")
    assert refreshed.purchase_price == Decimal("15.460000")
    assert db.get(CorporateAction, ca.id).status == "pending"
    assert db.execute(text("SELECT COUNT(*) FROM transactions WHERE id = :i"),
                      {"i": tx_id}).scalar() == 0
    assert db.execute(select(func.count()).select_from(CorporateActionApplication)
                      .where(CorporateActionApplication.corporate_action_id == ca.id)
                      ).scalar() == 0


def test_unapply_a_pending_event_is_rejected(db):
    ca = _pending(db)
    with pytest.raises(ValueError, match="not applied"):
        cas.unapply_action(db, ca.id)


def test_unapplying_the_cash_half_leaves_the_bonus_in_place(db):
    """A cash dividend never moved the lot, so reversing it must not either."""
    lot, bonus, cash_ca = _pan_same_day(db)
    cas.apply_action(db, bonus.id)

    cas.unapply_action(db, cash_ca.id)

    refreshed = portfolio_service.get_position(db, lot.id)
    assert refreshed.quantity == Decimal("79488.000000")
    assert refreshed.purchase_price == Decimal("18.891667")
    assert db.get(CorporateAction, bonus.id).status == "applied"
    assert db.get(CorporateAction, cash_ca.id).status == "pending"


def test_unapplying_the_bonus_half_restores_the_lot(db):
    lot, bonus, cash_ca = _pan_same_day(db)
    cas.apply_action(db, bonus.id)

    cas.unapply_action(db, bonus.id)

    refreshed = portfolio_service.get_position(db, lot.id)
    assert refreshed.quantity == Decimal("66240.000000")
    assert refreshed.purchase_price == Decimal("22.670000")


def test_ignore_marks_the_event_and_touches_nothing(db):
    db.execute(text("DELETE FROM positions"))
    lot = _lot(db, qty="1000", price="20")
    ca = _pending(db)

    assert cas.ignore_action(db, ca.id).status == "ignored"
    assert portfolio_service.get_position(db, lot.id).quantity == Decimal("1000.000000")


def _pan_same_day(db):
    """PAN 2026-05-29: a bonus and a cash dividend on one ex-date."""
    db.execute(text("DELETE FROM positions"))
    lot = _lot(db, ticker="PAN", qty="66240", price="22.67", bought="2026-01-01")
    bonus = _pending(db, symbol="PAN", ex_date=date(2026, 5, 29), ratio=Decimal("0.2"),
                     title="Thưởng cổ phiếu tỷ lệ 100:20")
    cash_ca = _pending(db, symbol="PAN", ex_date=date(2026, 5, 29),
                       name="Trả cổ tức bằng tiền mặt", action_type="cash", ratio=None,
                       amount_per_share=Decimal("3000"),
                       tax_withheld_pct=Decimal("0.05"),
                       title="Trả cổ tức năm 2026 bằng tiền 3000 đồng/CP")
    return lot, bonus, cash_ca


def test_applying_one_event_settles_its_whole_ex_date_group(db):
    """The cash must pay on the pre-bonus 66,240, not the post-bonus 79,488.

    Applying the two separately cannot achieve that — the first call has already
    moved the share count — so one apply covers the ex-date group.
    """
    lot, bonus, cash_ca = _pan_same_day(db)

    result = cas.apply_action(db, bonus.id)

    assert sorted(result.applied_action_ids) == sorted([bonus.id, cash_ca.id])
    assert result.total_cash_gross == Decimal("198720000")
    assert result.total_shares_added == Decimal("13248")
    refreshed = portfolio_service.get_position(db, lot.id)
    assert refreshed.quantity == Decimal("79488.000000")
    assert refreshed.purchase_price == Decimal("18.891667")
    assert db.get(CorporateAction, cash_ca.id).status == "applied"


def test_applying_the_cash_half_first_gives_the_same_result(db):
    """Whichever sibling the user clicks, the group settles identically."""
    lot, bonus, cash_ca = _pan_same_day(db)

    result = cas.apply_action(db, cash_ca.id)

    assert result.total_cash_gross == Decimal("198720000")
    assert portfolio_service.get_position(db, lot.id).quantity == Decimal("79488.000000")
    assert db.get(CorporateAction, bonus.id).status == "applied"


def test_applying_a_sibling_after_its_group_is_rejected(db):
    """The group already ran; the second click must not double-apply."""
    _lot_bonus_cash = _pan_same_day(db)
    _, bonus, cash_ca = _lot_bonus_cash
    cas.apply_action(db, bonus.id)

    with pytest.raises(ValueError, match="already applied"):
        cas.apply_action(db, cash_ca.id)


def test_a_different_ex_date_is_not_dragged_into_the_group(db):
    db.execute(text("DELETE FROM positions"))
    _lot(db, ticker="TST", qty="1000", price="20", bought="2025-01-01")
    first = _pending(db, ex_date=date(2026, 6, 1), ratio=Decimal("0.1"))
    later = _pending(db, ex_date=date(2026, 9, 1), ratio=Decimal("0.1"))

    result = cas.apply_action(db, first.id)

    assert result.applied_action_ids == [first.id]
    assert db.get(CorporateAction, later.id).status == "pending"


def test_another_symbol_on_the_same_ex_date_is_not_dragged_in(db):
    db.execute(text("DELETE FROM positions"))
    _lot(db, ticker="TST", qty="1000", price="20", bought="2025-01-01")
    mine = _pending(db, symbol="TST", ex_date=date(2026, 6, 1), ratio=Decimal("0.1"))
    other = _pending(db, symbol="OTH", ex_date=date(2026, 6, 1), ratio=Decimal("0.1"))

    result = cas.apply_action(db, mine.id)

    assert result.applied_action_ids == [mine.id]
    assert db.get(CorporateAction, other.id).status == "pending"


def test_an_unparsed_sibling_does_not_block_the_group(db):
    """It cannot be settled, so it stays for review rather than failing the group."""
    db.execute(text("DELETE FROM positions"))
    _lot(db, ticker="TST", qty="1000", price="20", bought="2025-01-01")
    good = _pending(db, ex_date=date(2026, 6, 1), ratio=Decimal("0.1"))
    bad = _pending(db, ex_date=date(2026, 6, 1), ratio=None, status="unparsed",
                   title="Thưởng cổ phiếu nothing here")

    result = cas.apply_action(db, good.id)

    assert result.applied_action_ids == [good.id]
    assert db.get(CorporateAction, bad.id).status == "unparsed"
