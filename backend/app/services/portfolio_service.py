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

from app.db.models.portfolio import Position, Transaction, InvestmentAmount
from app.schemas.portfolio import (
    PositionCreate,
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


def create_position(db: Session, position: PositionCreate) -> Position:
    db_position = Position(**position.model_dump())
    db.add(db_position)
    db.commit()
    db.refresh(db_position)
    return db_position


async def get_positions(db: Session) -> List[Position]:
    positions = db.query(Position).order_by(Position.ticker).all()
    if not positions:
        return positions

    # One price lookup per distinct ticker, all in flight together: the previous
    # sequential loop cost one full round trip per position, and a portfolio
    # holding the same ticker twice paid for it twice.
    tickers = list({p.ticker for p in positions})
    prices = await asyncio.gather(
        *(get_current_price(t) for t in tickers),
        return_exceptions=True,
    )
    by_ticker = {
        ticker: price
        for ticker, price in zip(tickers, prices)
        if not isinstance(price, BaseException) and price is not None
    }

    for position in positions:
        # ``current_price`` is a computed field, not a column — fall back to the
        # purchase price so an unreachable quote never nulls out the response.
        position.current_price = by_ticker.get(position.ticker, position.purchase_price)

    return positions


def get_position(db: Session, position_id: int) -> Optional[Position]:
    return db.query(Position).filter(Position.id == position_id).first()


def update_position(
    db: Session, position_id: int, position: PositionCreate
) -> Optional[Position]:
    db_position = get_position(db, position_id)
    if not db_position:
        return None

    for key, value in position.model_dump().items():
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
    db_position = (
        db.query(Position)
        .filter(Position.id == request.position_id)
        .with_for_update()
        .first()
    )
    if not db_position:
        raise ValueError(f"Position with ID {request.position_id} not found")

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


def delete_transaction(db: Session, transaction_id: int) -> bool:
    db_transaction = get_transaction(db, transaction_id)
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

    for pos in positions:
        pos_value = Decimal(str(pos.current_price)) * pos.quantity
        invested = pos.purchase_price * pos.quantity

        total_value += pos_value
        total_invested += invested

    total_profit_loss = total_value - total_invested
    total_profit_loss_pct = (
        ((total_value / total_invested) - 1) * 100
        if total_invested > 0
        else ZERO
    )

    # Realized P/L = trading gains on sells + net dividend income
    trading_pl = _calculate_realized_pl(db)
    dividend_gross, dividend_net = _calculate_dividend_income(db)

    return PortfolioSummary(
        total_value=total_value,
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
