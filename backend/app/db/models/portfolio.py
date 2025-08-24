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
    transaction_type = Column(Enum("buy", "sell"), nullable=False)
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
