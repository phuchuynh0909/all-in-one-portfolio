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
)
from app.services.stock_service import get_current_price


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
    
    return PortfolioSummary(
        total_value=total_value,
        total_invested=total_invested,
        total_profit_loss=total_profit_loss,
        total_profit_loss_pct=total_profit_loss_pct,
        positions=positions,
    )
