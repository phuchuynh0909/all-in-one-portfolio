"""
SQLAlchemy models for financial data storage

These models represent Vietnamese financial statement data with hierarchical structure.
"""

from sqlalchemy import (
    Column, Integer, String, Date, TIMESTAMP, NUMERIC, Text,
    ForeignKey, UniqueConstraint, CheckConstraint, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class Company(Base):
    """Companies being reported on"""
    
    __tablename__ = "company"
    
    company_id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String(20), unique=True, nullable=True, index=True)
    name = Column(String(255), nullable=False)
    
    # Relationships
    item_values = relationship("ItemValue", back_populates="company")
    
    def __repr__(self):
        return f"<Company(ticker='{self.ticker}', name='{self.name}')>"


class Period(Base):
    """Reporting periods (quarters, years, etc.)"""
    
    __tablename__ = "period"
    
    period_id = Column(Integer, primary_key=True, autoincrement=True)
    label = Column(String(32), unique=True, nullable=False, index=True)  # e.g., 'Q2-2025'
    period_type = Column(String(16), nullable=False, index=True)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True, index=True)
    
    # Constraints
    __table_args__ = (
        CheckConstraint("period_type IN ('quarter','year','month','other')", name='period_type_check'),
    )
    
    # Relationships
    item_values = relationship("ItemValue", back_populates="period")
    
    def __repr__(self):
        return f"<Period(label='{self.label}', type='{self.period_type}')>"


class Statement(Base):
    """Financial statement types - master data shared across all companies"""
    
    __tablename__ = "statement"
    
    statement_id = Column(Integer, primary_key=True, autoincrement=True)
    statement_type = Column(String(32), nullable=False, index=True, unique=True)
    title = Column(String(255), nullable=True)  # optional display title
    
    # Constraints
    __table_args__ = (
        CheckConstraint(
            "statement_type IN ('candoiketoan','baocaothunhap','luuchuyentiente','thuyetminh')", 
            name='statement_type_check'
        ),
    )
    
    # Relationships
    statement_items = relationship("StatementItem", back_populates="statement", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Statement(type='{self.statement_type}', title='{self.title}')>"


class StatementItem(Base):
    """Individual line items in financial statements - master data shared across all companies"""
    
    __tablename__ = "statement_item"
    
    item_id = Column(Integer, primary_key=True, autoincrement=True)
    statement_id = Column(Integer, ForeignKey("statement.statement_id", ondelete="CASCADE"), nullable=False, index=True)
    # 255 keeps the (statement_id, item_key) unique index inside InnoDB's
    # 3072-byte key limit at utf8mb4's 4 bytes per character.
    item_key = Column(String(255), nullable=False, index=True)  # stable idempotent key
    title_vi = Column(String(500), nullable=False)  # Vietnamese title
    level = Column(Integer, nullable=False, index=True)  # hierarchy level
    parent_item_id = Column(Integer, ForeignKey("statement_item.item_id"), nullable=True, index=True)
    display_order = Column(Integer, nullable=True)  # optional: keep original order
    
    # Constraints
    __table_args__ = (
        UniqueConstraint('statement_id', 'item_key'),
    )
    
    # Relationships
    statement = relationship("Statement", back_populates="statement_items")
    parent_item = relationship("StatementItem", remote_side=[item_id], backref="child_items")
    item_values = relationship("ItemValue", back_populates="statement_item", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<StatementItem(key='{self.item_key}', level={self.level}, title='{self.title_vi[:50]}...')>"
    
    @property
    def indented_title(self):
        """Return title with indentation based on level"""
        return "  " * (self.level - 1) + self.title_vi


class ItemValue(Base):
    """Actual numeric values for each line item per period per company"""
    
    __tablename__ = "item_value"
    
    item_value_id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, ForeignKey("statement_item.item_id", ondelete="CASCADE"), nullable=False, index=True)
    period_id = Column(Integer, ForeignKey("period.period_id"), nullable=False, index=True)
    company_id = Column(Integer, ForeignKey("company.company_id"), nullable=False, index=True)
    value = Column(NUMERIC(precision=20, scale=1), nullable=False)  # adjust scale/precision as needed
    
    # Constraints
    __table_args__ = (
        UniqueConstraint('item_id', 'period_id', 'company_id'),
    )
    
    # Relationships
    statement_item = relationship("StatementItem", back_populates="item_values")
    period = relationship("Period", back_populates="item_values")
    company = relationship("Company", back_populates="item_values")
    
    def __repr__(self):
        return f"<ItemValue(company='{self.company.ticker if self.company else None}', item='{self.statement_item.item_key if self.statement_item else None}', period='{self.period.label if self.period else None}', value={self.value})>"


# Additional indexes for performance
Index('ix_item_value_composite', ItemValue.item_id, ItemValue.period_id, ItemValue.company_id)
Index('ix_statement_item_level_order', StatementItem.level, StatementItem.display_order)
