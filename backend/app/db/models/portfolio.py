from datetime import datetime
from decimal import Decimal
from sqlalchemy import Column, Integer, String, DECIMAL, Date, Text, TIMESTAMP, Enum, text
from app.db.base import Base


class Position(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), nullable=False)
    quantity = Column(DECIMAL(15, 6), nullable=False)
    purchase_price = Column(DECIMAL(15, 6), nullable=False)
    purchase_date = Column(Date, nullable=False)
    notes = Column(Text)
    created_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(10), nullable=False)
    # Four values, matching the widened ENUM from migration d5a91c3e7b20.
    transaction_type = Column(
        Enum("buy", "sell", "dividend_cash", "dividend_stock"), nullable=False
    )
    quantity = Column(DECIMAL(15, 6), nullable=False)
    price = Column(DECIMAL(15, 6), nullable=False)
    close_price = Column(DECIMAL(15, 6))
    transaction_date = Column(Date, nullable=False)
    fees = Column(DECIMAL(10, 2), server_default=text("0"))
    notes = Column(Text)
    created_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))


class InvestmentAmount(Base):
    __tablename__ = "investment_amounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    amount = Column(DECIMAL(15, 2), nullable=False)
    date = Column(Date, nullable=False)
    notes = Column(Text)
    created_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))


class PriceAlert(Base):
    """Price alert model for tracking symbol price conditions."""
    __tablename__ = "price_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    condition = Column(Enum("gt", "gte", "lt", "lte", "eq", name="alert_condition"), nullable=False)
    target_price = Column(DECIMAL(15, 6), nullable=False)
    is_active = Column(Integer, server_default=text("1"), nullable=False, index=True)  # 1 = active, 0 = inactive
    is_triggered = Column(Integer, server_default=text("0"), nullable=False)  # 1 = triggered, 0 = not triggered
    triggered_at = Column(TIMESTAMP, nullable=True)
    notes = Column(Text)
    created_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
