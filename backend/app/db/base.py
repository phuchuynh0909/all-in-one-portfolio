from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import StaticPool

from app.core.settings import settings

# SQLAlchemy models base class
Base = declarative_base()

# Import all models to ensure they're registered with SQLAlchemy
from app.db.models.portfolio import Position, Transaction, InvestmentAmount
from app.db.models.market import Sector, StockSymbol
from app.db.models.report import ReportSummary

# Create SQLite engine with thread-safe connection pool
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
    poolclass=StaticPool,
    echo=settings.environment == "development"
)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Dependency for getting database sessions."""
    print(settings.database_url)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
