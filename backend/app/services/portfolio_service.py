from datetime import date
from decimal import Decimal
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

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
from datetime import datetime, timedelta

def create_position(db: Session, position: PositionCreate) -> Position:
    db_position = Position(**position.model_dump())
    db.add(db_position)
    db.commit()
    db.refresh(db_position)
    return db_position


async def get_positions(db: Session) -> List[Position]:
    positions = db.query(Position).order_by(Position.ticker).all()
    
    # Get current prices for all positions
    for position in positions:
        current_price = await get_current_price(position.ticker)
        position.current_price = current_price if current_price is not None else position.purchase_price
    
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
    
    Args:
        db: Database session
        request: Close position request with details
        
    Returns:
        ClosePositionResponse with operation details
        
    Raises:
        ValueError: If position not found or insufficient quantity
    """
    # Get the position
    db_position = get_position(db, request.position_id)
    if not db_position:
        raise ValueError(f"Position with ID {request.position_id} not found")
    
    # Validate quantity
    if request.quantity_to_close > db_position.quantity:
        raise ValueError(
            f"Cannot close {request.quantity_to_close} shares. "
            f"Position only has {db_position.quantity} shares available."
        )
    
    # Calculate realized P/L
    purchase_value = db_position.purchase_price * request.quantity_to_close
    closing_value = request.closing_price * request.quantity_to_close
    realized_pl = closing_value - purchase_value - (request.fees or Decimal(0))
    realized_pl_pct = ((request.closing_price / db_position.purchase_price) - 1) * 100
    
    # Create sell transaction
    transaction_data = TransactionCreate(
        ticker=db_position.ticker,
        transaction_type="sell",
        quantity=request.quantity_to_close,
        price=db_position.purchase_price,
        close_price=request.closing_price,
        transaction_date=request.closing_date,
        fees=request.fees or Decimal(0),
        notes=request.notes or f"Position closure - Realized P/L: {realized_pl}"
    )
    
    db_transaction = create_transaction(db, transaction_data)
    
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
    
    db.commit()
    
    return ClosePositionResponse(
        success=True,
        message=f"Successfully closed {request.quantity_to_close} shares of {db_position.ticker}",
        position_updated=position_updated,
        remaining_quantity=remaining_quantity,
        transaction_id=db_transaction.id,
        realized_pl=realized_pl,
        realized_pl_pct=realized_pl_pct
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
    
    total_value = Decimal(0)
    total_invested = Decimal(0)
    
    for pos in positions:
        pos_value = Decimal(str(pos.current_price)) * pos.quantity
        invested = pos.purchase_price * pos.quantity
        
        total_value += pos_value
        total_invested += invested
    
    total_profit_loss = total_value - total_invested
    total_profit_loss_pct = (
        ((total_value / total_invested) - 1) * 100
        if total_invested > 0
        else Decimal(0)
    )
    
    # Calculate realized P/L from sell transactions
    total_realized_pl = _calculate_realized_pl(db)
    
    return PortfolioSummary(
        total_value=total_value,
        total_invested=total_invested,
        total_profit_loss=total_profit_loss,
        total_profit_loss_pct=total_profit_loss_pct,
        total_realized_pl=total_realized_pl,
        positions=positions,
    )


def _calculate_realized_pl(db: Session) -> Decimal:
    """Calculate total realized profit/loss from all sell transactions."""
    # Get all sell transactions
    sell_transactions = (
        db.query(Transaction)
        .filter(Transaction.transaction_type == "sell")
        .all()
    )
    
    total_realized_pl = Decimal(0)
    
    for transaction in sell_transactions:
        # For sell transactions, realized P/L is calculated as:
        # (close_price - purchase_price) * quantity - fees
        # If close_price is None, we use the transaction price as both buy and sell price (no gain/loss)
        if transaction.close_price is not None:
            # Realized P/L = (selling_price - purchase_price) * quantity - fees
            selling_price = transaction.close_price
            purchase_price = transaction.price
            realized_pl = (selling_price - purchase_price) * transaction.quantity - (transaction.fees or Decimal(0))
        else:
            # If no close_price, assume no realized gain/loss, just deduct fees
            realized_pl = -(transaction.fees or Decimal(0))
        
        total_realized_pl += realized_pl
    
    return total_realized_pl


def _load_price_history(db: Session, tickers: list[str], start: date, end: date) -> pd.DataFrame:
    """Load historical prices for given tickers from transactions table fallback.

    This is a basic implementation placeholder. In a real setup, you'd source
    OHLCV from your Delta Lake or an external provider. Here we compute daily
    price using latest known close_price or purchase price as proxy.
    """
    # Fallback: use latest prices from positions if available
    from app.db.models.portfolio import Position
    rows = (
        db.query(Position)
        .filter(Position.ticker.in_(tickers))
        .all()
    )
    # Build a dummy price matrix with constant prices over date range
    dates = pd.date_range(start=start, end=end, freq="B")
    data = {}
    for row in rows:
        price = float(row.current_price or row.purchase_price)
        data[row.ticker] = np.full(len(dates), price)
    df = pd.DataFrame(data, index=dates)
    df.index.name = "date"
    return df
