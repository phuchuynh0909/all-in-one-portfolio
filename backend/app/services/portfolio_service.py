"""Portfolio reads and writes against MySQL (``my_portfolio``).

Migrated off the single-file SQLite ``portfolio.db``; see
``scripts/migrate_portfolio_db_to_mysql.py`` and alembic revision
``c4d8e1f60b93``. The ORM calls here are dialect-neutral, but three things that
SQLite let slide matter on a networked InnoDB server and drove the shape of this
module:

* **Multi-statement writes are one transaction.** ``close_position`` records a
  sell and mutates the position; committing those separately can leave a sell
  booked against an unchanged position if the second commit fails.
* **Aggregates run in the database.** Realized P/L is a ``SUM`` over
  ``transactions``, not a Python loop over every row ever fetched across the
  wire. MySQL's ``DECIMAL`` arithmetic is exact, so the result is identical.
* **Per-row round trips are batched or gathered.** Price lookups for the
  position list run concurrently rather than one after another.
"""
import asyncio
from datetime import date
from decimal import Decimal
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from app.db.models.portfolio import Position, Transaction, InvestmentAmount
from app.db.models.corporate_action import CorporateAction, CorporateActionApplication
from app.schemas.portfolio import (
    PositionCreate,
    PositionUpdate,
    TransactionCreate,
    InvestmentAmountCreate,
    PortfolioSummary,
    ClosePositionRequest,
    ClosePositionResponse,
)
from app.services.stock_service import get_current_price
import pandas as pd
import numpy as np

ZERO = Decimal(0)


def _lock_applied_actions(db: Session, tickers: set[str]) -> list[CorporateAction]:
    actions = db.query(CorporateAction).filter(
        func.upper(CorporateAction.symbol).in_({ticker.strip().upper() for ticker in tickers}),
    ).order_by(CorporateAction.id).populate_existing().with_for_update().all()
    return [action for action in actions if action.status == "applied"]


def create_position(db: Session, position: PositionCreate) -> Position:
    actions = _lock_applied_actions(db, {position.ticker})
    if any(action.ex_date >= position.purchase_date for action in actions):
        raise ValueError("Reverse applied corporate actions before adding this historical position")
    db_position = Position(**position.model_dump())
    db.add(db_position)
    db.commit()
    db.refresh(db_position)
    return db_position


async def get_positions(db: Session) -> List[Position]:
    positions = await run_in_threadpool(lambda: db.query(Position).order_by(Position.ticker).all())
    if not positions:
        return positions

    # One price lookup per distinct ticker, all in flight together: the previous
    # sequential loop cost one full round trip per position, and a portfolio
    # holding the same ticker twice paid for it twice.
    tickers = list({p.ticker for p in positions})
    prices = await asyncio.gather(
        *(get_current_price(t, include_date=True) for t in tickers),
        return_exceptions=True,
    )
    by_ticker = {
        ticker: price
        for ticker, price in zip(tickers, prices)
        if not isinstance(price, BaseException) and price is not None
    }

    for position in positions:
        quote = by_ticker.get(position.ticker)
        position.current_price = quote["price"] if quote else None
        position.current_price_date = quote["date"] if quote else None

    return positions


def get_position(db: Session, position_id: int) -> Optional[Position]:
    return db.query(Position).filter(Position.id == position_id).first()


def update_position(
    db: Session, position_id: int, position: PositionUpdate
) -> Optional[Position]:
    current = get_position(db, position_id)
    if current is None:
        return None
    original_ticker = current.ticker
    actions = _lock_applied_actions(db, {original_ticker, position.ticker})
    db_position = db.query(Position).filter(Position.id == position_id).populate_existing().with_for_update().first()
    if not db_position:
        return None

    if db_position.ticker != original_ticker or any(getattr(db_position, key) != value for key, value in position.original.model_dump().items()):
        raise ValueError("Position has changed; refresh the page before saving")

    fields = ("ticker", "quantity", "purchase_price", "purchase_date")
    changed = any(getattr(db_position, key) != getattr(position, key) for key in fields)
    if changed and actions:
        linked = db.query(CorporateActionApplication.id).filter(
            CorporateActionApplication.position_id == position_id,
            CorporateActionApplication.corporate_action_id.in_([action.id for action in actions]),
        ).first()
        if linked or any(action.ex_date >= min(db_position.purchase_date, position.purchase_date) for action in actions):
            raise ValueError("Reverse applied corporate actions before changing this position")

    for key, value in position.model_dump(exclude={"original"}).items():
        setattr(db_position, key, value)

    db.commit()
    db.refresh(db_position)
    return db_position


def delete_position(db: Session, position_id: int) -> bool:
    db_position = get_position(db, position_id)
    if not db_position:
        return False

    db.delete(db_position)
    db.commit()
    return True


def close_position(db: Session, request: ClosePositionRequest) -> ClosePositionResponse:
    """
    Close a position (partially or fully) and create a corresponding sell transaction.

    The sell transaction and the position update are one transaction: on MySQL a
    failure between them would otherwise leave a sell booked against a position
    that still shows its full quantity. Nothing is committed until both succeed.

    Args:
        db: Database session
        request: Close position request with details

    Returns:
        ClosePositionResponse with operation details

    Raises:
        ValueError: If position not found or insufficient quantity
    """
    # Lock the row for the duration of the transaction so two concurrent closes
    # cannot both read the same quantity and each pass the check below.
    current = get_position(db, request.position_id)
    if current is None:
        raise ValueError(f"Position with ID {request.position_id} not found")
    original_ticker = current.ticker
    actions = _lock_applied_actions(db, {original_ticker})
    db_position = (
        db.query(Position)
        .filter(Position.id == request.position_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if not db_position:
        raise ValueError(f"Position with ID {request.position_id} not found")

    if db_position.ticker != original_ticker:
        raise ValueError("Position has changed; refresh the page before closing")

    if request.closing_date < db_position.purchase_date:
        raise ValueError("Closing date cannot be before the purchase date")
    if any(action.ex_date >= request.closing_date for action in actions):
        raise ValueError("Reverse applied corporate actions before recording this sale")

    # Validate quantity
    if request.quantity_to_close > db_position.quantity:
        raise ValueError(
            f"Cannot close {request.quantity_to_close} shares. "
            f"Position only has {db_position.quantity} shares available."
        )

    # Read what the response needs before the position is deleted: after the
    # commit below a deleted instance is detached and its attributes are gone.
    ticker = db_position.ticker
    purchase_price = db_position.purchase_price
    fees = request.fees or ZERO

    # Calculate realized P/L
    purchase_value = purchase_price * request.quantity_to_close
    closing_value = request.closing_price * request.quantity_to_close
    realized_pl = closing_value - purchase_value - fees
    realized_pl_pct = ((request.closing_price / purchase_price) - 1) * 100

    try:
        # Create sell transaction — added to this transaction, not committed on
        # its own (hence not ``create_transaction``, which commits).
        db_transaction = Transaction(
            ticker=ticker,
            transaction_type="sell",
            is_position_close=True,
            quantity=request.quantity_to_close,
            price=purchase_price,
            close_price=request.closing_price,
            transaction_date=request.closing_date,
            fees=fees,
            notes=request.notes or f"Position closure - Realized P/L: {realized_pl}",
        )
        db.add(db_transaction)

        # Update or delete position
        remaining_quantity = db_position.quantity - request.quantity_to_close
        position_updated = True

        if remaining_quantity == 0:
            # Delete position completely
            db.delete(db_position)
            position_updated = False
            remaining_quantity = None
        else:
            # Update position with remaining quantity
            db_position.quantity = remaining_quantity

        # ``flush`` assigns the transaction's autoincrement id while still inside
        # the transaction, so the response can carry it.
        db.flush()
        transaction_id = db_transaction.id
        db.commit()
    except Exception:
        db.rollback()
        raise

    return ClosePositionResponse(
        success=True,
        message=f"Successfully closed {request.quantity_to_close} shares of {ticker}",
        position_updated=position_updated,
        remaining_quantity=remaining_quantity,
        transaction_id=transaction_id,
        realized_pl=realized_pl,
        realized_pl_pct=realized_pl_pct,
    )


def create_transaction(db: Session, transaction: TransactionCreate) -> Transaction:
    if transaction.transaction_type in ("dividend_cash", "dividend_stock"):
        raise ValueError("Dividends must be recorded through corporate actions")
    db_transaction = Transaction(**transaction.model_dump())
    db.add(db_transaction)
    db.commit()
    db.refresh(db_transaction)
    return db_transaction


def get_transactions(db: Session) -> List[Transaction]:
    return (
        db.query(Transaction)
        .order_by(Transaction.transaction_date.desc())
        .all()
    )


def get_transaction(db: Session, transaction_id: int) -> Optional[Transaction]:
    return db.query(Transaction).filter(Transaction.id == transaction_id).first()


def _editable_transaction(db: Session, transaction_id: int) -> Optional[Transaction]:
    db_transaction = db.query(Transaction).filter(Transaction.id == transaction_id).with_for_update().first()
    if db_transaction and (db_transaction.is_position_close or db_transaction.transaction_type in ("dividend_cash", "dividend_stock")):
        raise ValueError("This transaction is linked to a position close or corporate action and cannot be edited or deleted independently")
    return db_transaction


def update_transaction(db: Session, transaction_id: int, transaction: TransactionCreate) -> Optional[Transaction]:
    db_transaction = _editable_transaction(db, transaction_id)
    if db_transaction is None:
        return None
    if transaction.transaction_type in ("dividend_cash", "dividend_stock"):
        raise ValueError("Dividend transactions cannot be created through an edit")
    for key, value in transaction.model_dump().items():
        setattr(db_transaction, key, value)
    db.commit()
    db.refresh(db_transaction)
    return db_transaction


def delete_transaction(db: Session, transaction_id: int) -> bool:
    db_transaction = _editable_transaction(db, transaction_id)
    if not db_transaction:
        return False

    db.delete(db_transaction)
    db.commit()
    return True


def set_investment_amount(
    db: Session, investment: InvestmentAmountCreate
) -> InvestmentAmount:
    # Update or create investment amount
    db_investment = (
        db.query(InvestmentAmount)
        .order_by(InvestmentAmount.date.desc())
        .first()
    )

    if db_investment:
        for key, value in investment.model_dump().items():
            setattr(db_investment, key, value)
    else:
        db_investment = InvestmentAmount(**investment.model_dump())
        db.add(db_investment)

    db.commit()
    db.refresh(db_investment)
    return db_investment


def get_investment_amount(db: Session) -> Optional[InvestmentAmount]:
    return (
        db.query(InvestmentAmount)
        .order_by(InvestmentAmount.date.desc())
        .first()
    )


async def get_portfolio_summary(db: Session) -> PortfolioSummary:
    positions = await get_positions(db)

    total_value = ZERO
    total_invested = ZERO
    missing_prices = False

    for pos in positions:
        invested = pos.purchase_price * pos.quantity
        total_invested += invested
        if pos.current_price is None:
            missing_prices = True
        else:
            total_value += Decimal(str(pos.current_price)) * pos.quantity

    total_profit_loss = None if missing_prices else total_value - total_invested
    total_profit_loss_pct = (
        None if missing_prices else ((total_value / total_invested) - 1) * 100
        if total_invested > 0
        else ZERO
    )

    # Realized P/L = trading gains on sells + net dividend income
    trading_pl, (dividend_gross, dividend_net) = await run_in_threadpool(
        lambda: (_calculate_realized_pl(db), _calculate_dividend_income(db))
    )

    return PortfolioSummary(
        total_value=None if missing_prices else total_value,
        total_invested=total_invested,
        total_profit_loss=total_profit_loss,
        total_profit_loss_pct=total_profit_loss_pct,
        total_realized_pl=trading_pl + dividend_net,
        total_dividend_income_gross=dividend_gross,
        total_dividend_income=dividend_net,
        positions=positions,
    )


def _calculate_realized_pl(db: Session) -> Decimal:
    """Total realized profit/loss across all sell transactions.

    One ``SUM`` in MySQL rather than fetching every sell transaction and adding
    them up in Python — the table grows without bound, the total does not.
    ``DECIMAL`` arithmetic in MySQL is exact, so this matches the previous
    row-by-row result to the cent.

    Per row: ``(close_price - price) * quantity - fees``. A row with no
    ``close_price`` records no gain or loss, only the fee — expressed here with
    ``COALESCE(close_price, price)``, which zeroes the price delta for it.
    """
    fees = func.coalesce(Transaction.fees, 0)
    close = func.coalesce(Transaction.close_price, Transaction.price)
    realized = (close - Transaction.price) * Transaction.quantity - fees

    total = db.execute(
        select(func.coalesce(func.sum(realized), 0)).where(
            Transaction.transaction_type == "sell"
        )
    ).scalar()

    # ``SUM`` over DECIMAL comes back as Decimal; NULL is already coalesced away.
    return Decimal(str(total)) if total is not None else ZERO


def _load_price_history(db: Session, tickers: list[str], start: date, end: date) -> pd.DataFrame:
    """Load historical prices for given tickers from transactions table fallback.

    This is a basic implementation placeholder. In a real setup, you'd source
    OHLCV from your Delta Lake or an external provider. Here we compute daily
    price using latest known close_price or purchase price as proxy.
    """
    # Fallback: use latest prices from positions if available
    rows = (
        db.query(Position)
        .filter(Position.ticker.in_(tickers))
        .all()
    )
    # Build a dummy price matrix with constant prices over date range
    dates = pd.date_range(start=start, end=end, freq="B")
    data = {}
    for row in rows:
        # ``current_price`` only exists on instances that went through
        # ``get_positions``; these came straight from the query, so read it
        # defensively rather than raising AttributeError.
        price = float(getattr(row, "current_price", None) or row.purchase_price)
        data[row.ticker] = np.full(len(dates), price)
    df = pd.DataFrame(data, index=dates)
    df.index.name = "date"
    return df


def _calculate_dividend_income(db: Session) -> tuple[Decimal, Decimal]:
    """Cash dividend income, ``(gross, net)``.

    Read from the application ledger rather than the ``transactions`` rows,
    because the withholding rate lives on the event and the ledger already
    holds the gross amount per lot. Stock events carry a NULL ``cash_amount``
    and so contribute nothing.
    """
    from app.db.models.corporate_action import (
        CorporateAction,
        CorporateActionApplication,
    )

    cash = func.coalesce(CorporateActionApplication.cash_amount, 0)
    kept = 1 - func.coalesce(CorporateAction.tax_withheld_pct, 0)

    row = db.execute(
        select(
            func.coalesce(func.sum(cash), 0),
            func.coalesce(func.sum(cash * kept), 0),
        )
        .select_from(CorporateActionApplication)
        .join(
            CorporateAction,
            CorporateAction.id == CorporateActionApplication.corporate_action_id,
        )
        .where(CorporateAction.action_type == "cash")
    ).one()

    return Decimal(str(row[0])), Decimal(str(row[1]))
