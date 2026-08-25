"""End-to-end checks for ``portfolio_service`` against the MySQL store.

These run real SQL against ``my_portfolio`` — the point is to catch the things a
SQLite-backed test cannot: MySQL's ``DECIMAL`` semantics, ``SELECT … FOR UPDATE``,
native ``ENUM`` columns, and whether ``close_position`` is genuinely one
transaction. The whole module is skipped when the server is unreachable.

Every test works inside its own transaction and rolls back, so the tables are
left exactly as they were found.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.settings import settings
from app.db.base import SessionLocal, engine
from app.schemas.portfolio import (
    ClosePositionRequest,
    PositionCreate,
    TransactionCreate,
)
from app.services import portfolio_service as svc


def _mysql_available() -> bool:
    if not settings.database_url.startswith("mysql"):
        return False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


pytestmark = pytest.mark.skipif(
    not _mysql_available(),
    reason=f"MySQL not reachable at {settings.mysql_host}:{settings.mysql_port}",
)


@pytest.fixture
def db():
    """A session whose work is always rolled back.

    The service functions call ``commit()``, which would normally end the outer
    transaction. Binding the session to a connection that already has one open
    nests them, so the outer ``rollback()`` still discards everything.
    """
    connection = engine.connect()
    outer = connection.begin()
    session = SessionLocal(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


def _position(ticker: str = "TST", qty: str = "100", price: str = "25.5") -> PositionCreate:
    return PositionCreate(
        ticker=ticker,
        quantity=Decimal(qty),
        purchase_price=Decimal(price),
        purchase_date=date(2025, 1, 15),
        notes="pytest fixture",
    )


def test_position_roundtrip_preserves_decimals(db):
    """DECIMAL(15,6) must come back exact, not as a float approximation."""
    created = svc.create_position(db, _position(qty="100.5", price="25.123456"))

    assert created.id is not None
    fetched = svc.get_position(db, created.id)
    assert fetched is not None
    assert fetched.quantity == Decimal("100.500000")
    assert fetched.purchase_price == Decimal("25.123456")
    assert isinstance(fetched.purchase_price, Decimal)
    # server_default CURRENT_TIMESTAMP is populated by MySQL, not by Python
    assert fetched.created_at is not None


def test_transaction_type_enum_accepts_buy_and_sell(db):
    """``transaction_type`` is a native MySQL ENUM after the migration."""
    for kind in ("buy", "sell"):
        created = svc.create_transaction(
            db,
            TransactionCreate(
                ticker="TST",
                transaction_type=kind,
                quantity=Decimal("10"),
                price=Decimal("20"),
                transaction_date=date(2025, 2, 1),
                fees=Decimal("1.5"),
            ),
        )
        assert svc.get_transaction(db, created.id).transaction_type == kind


def test_partial_close_updates_quantity_and_books_a_sell(db):
    position = svc.create_position(db, _position(qty="100", price="20"))

    result = svc.close_position(
        db,
        ClosePositionRequest(
            position_id=position.id,
            quantity_to_close=Decimal("40"),
            closing_price=Decimal("25"),
            closing_date=date(2025, 3, 1),
            fees=Decimal("2"),
        ),
    )

    assert result.success is True
    assert result.position_updated is True
    assert result.remaining_quantity == Decimal("60")
    # (25 - 20) * 40 - 2
    assert result.realized_pl == Decimal("198")
    assert result.realized_pl_pct == Decimal("25")

    assert svc.get_position(db, position.id).quantity == Decimal("60.000000")
    booked = svc.get_transaction(db, result.transaction_id)
    assert booked.transaction_type == "sell"
    assert booked.quantity == Decimal("40.000000")
    assert booked.close_price == Decimal("25.000000")


def test_full_close_deletes_position_and_still_names_the_ticker(db):
    """The response reads the ticker after the position row is gone.

    Regression guard: the values the message needs are captured before the
    commit, so the deleted (detached) instance is never touched afterwards.
    """
    position = svc.create_position(db, _position(ticker="ZZZ", qty="50", price="10"))

    result = svc.close_position(
        db,
        ClosePositionRequest(
            position_id=position.id,
            quantity_to_close=Decimal("50"),
            closing_price=Decimal("12"),
            closing_date=date(2025, 3, 2),
            fees=Decimal("0"),
        ),
    )

    assert result.position_updated is False
    assert result.remaining_quantity is None
    assert "ZZZ" in result.message
    assert result.realized_pl == Decimal("100")
    assert svc.get_position(db, position.id) is None


def test_overclose_is_rejected_and_writes_nothing(db):
    """The guard must leave no half-finished sell behind."""
    position = svc.create_position(db, _position(qty="10", price="10"))
    before = len(svc.get_transactions(db))

    with pytest.raises(ValueError, match="Cannot close"):
        svc.close_position(
            db,
            ClosePositionRequest(
                position_id=position.id,
                quantity_to_close=Decimal("999"),
                closing_price=Decimal("11"),
                closing_date=date(2025, 3, 3),
            ),
        )

    assert len(svc.get_transactions(db)) == before
    assert svc.get_position(db, position.id).quantity == Decimal("10.000000")


def test_realized_pl_aggregate_matches_a_python_sum(db):
    """The SQL ``SUM`` must equal the row-by-row total it replaced.

    Measured as a delta against whatever the table already holds, so this is
    correct on a populated database as well as an empty one.
    """
    baseline = svc._calculate_realized_pl(db)
    rows = [
        # (quantity, price, close_price, fees)
        ("10", "20", "25", "1"),      # +49
        ("5", "30", "28", "0.5"),     # -10.5
        ("7", "15", None, "2"),       # -2  (no close_price: fee only)
    ]
    for qty, price, close, fees in rows:
        svc.create_transaction(
            db,
            TransactionCreate(
                ticker="AGG",
                transaction_type="sell",
                quantity=Decimal(qty),
                price=Decimal(price),
                close_price=Decimal(close) if close is not None else None,
                transaction_date=date(2025, 4, 1),
                fees=Decimal(fees),
            ),
        )
    # A buy must not be counted at all.
    svc.create_transaction(
        db,
        TransactionCreate(
            ticker="AGG",
            transaction_type="buy",
            quantity=Decimal("100"),
            price=Decimal("5"),
            transaction_date=date(2025, 4, 2),
            fees=Decimal("99"),
        ),
    )

    expected = sum(
        (
            (Decimal(c) - Decimal(p)) * Decimal(q) - Decimal(f)
            if c is not None
            else -Decimal(f)
        )
        for q, p, c, f in rows
    )
    aggregate = svc._calculate_realized_pl(db)

    assert expected == Decimal("36.5")
    assert aggregate - baseline == expected
    assert isinstance(aggregate, Decimal)


def test_realized_pl_is_zero_when_there_are_no_sells(db):
    db.execute(text("DELETE FROM transactions"))
    assert svc._calculate_realized_pl(db) == Decimal(0)


async def test_get_positions_fills_current_price_concurrently(db, monkeypatch):
    """Prices are gathered per distinct ticker, and a failure falls back."""
    calls: list[str] = []

    async def fake_price(ticker: str):
        calls.append(ticker)
        if ticker == "BAD":
            raise RuntimeError("quote feed down")
        return Decimal("99.5")

    monkeypatch.setattr(svc, "get_current_price", fake_price)

    svc.create_position(db, _position(ticker="AAA", qty="10", price="20"))
    svc.create_position(db, _position(ticker="AAA", qty="5", price="21"))
    svc.create_position(db, _position(ticker="BAD", qty="3", price="7.25"))

    positions = await svc.get_positions(db)
    by_ticker = {p.ticker: p for p in positions}

    # Three rows across two distinct tickers, but one lookup each — asserted per
    # ticker rather than on the whole list, so pre-existing positions in the
    # table do not make this brittle.
    assert calls.count("AAA") == 1
    assert calls.count("BAD") == 1
    assert by_ticker["AAA"].current_price == Decimal("99.5")
    # The failing quote falls back to that position's own purchase price.
    assert by_ticker["BAD"].current_price == Decimal("7.250000")


async def test_portfolio_summary_totals(db, monkeypatch):
    async def fake_price(ticker: str):
        return Decimal("30")

    monkeypatch.setattr(svc, "get_current_price", fake_price)
    db.execute(text("DELETE FROM transactions"))
    db.execute(text("DELETE FROM positions"))

    svc.create_position(db, _position(ticker="SUM", qty="10", price="20"))

    summary = await svc.get_portfolio_summary(db)

    assert summary.total_value == Decimal("300")
    assert summary.total_invested == Decimal("200")
    assert summary.total_profit_loss == Decimal("100")
    assert summary.total_profit_loss_pct == Decimal("50")
    assert summary.total_realized_pl == Decimal("0")
    assert len(summary.positions) == 1


def test_investment_amount_upsert_keeps_one_row(db):
    from app.schemas.portfolio import InvestmentAmountCreate

    db.execute(text("DELETE FROM investment_amounts"))

    first = svc.set_investment_amount(
        db, InvestmentAmountCreate(amount=Decimal("1000.50"), date=date(2025, 1, 1))
    )
    second = svc.set_investment_amount(
        db, InvestmentAmountCreate(amount=Decimal("2500.75"), date=date(2025, 6, 1))
    )

    assert first.id == second.id  # updated in place, not inserted
    current = svc.get_investment_amount(db)
    assert current.amount == Decimal("2500.75")
    assert current.date == date(2025, 6, 1)
    assert db.execute(text("SELECT COUNT(*) FROM investment_amounts")).scalar() == 1
