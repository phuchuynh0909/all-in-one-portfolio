from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import StaticPool

from app.core.settings import settings

# SQLAlchemy models base class
Base = declarative_base()

# Import all models to ensure they're registered with SQLAlchemy
from app.db.models.portfolio import Position, Transaction, InvestmentAmount
from app.db.models.market import Sector, StockSymbol
from app.db.models.corporate_action import CorporateAction, CorporateActionApplication
from app.db.models.user import User
from app.db.models.tcbs import TcbsOAuthToken


def _engine_kwargs(url: str) -> dict:
    """Pool/connect settings for the URL's dialect.

    SQLite (tests, and the retired ``portfolio.db``) needs
    ``check_same_thread=False`` plus ``StaticPool`` so every thread shares the
    one file handle. MySQL wants the opposite: a real pool, ``pool_pre_ping`` so
    a connection the server dropped is replaced instead of raising, and
    ``pool_recycle`` under MySQL's default 8-hour ``wait_timeout``. Reusing
    SQLite's StaticPool here would serialise every request onto one connection.
    """
    if url.startswith("sqlite"):
        return {
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        }
    return {
        "pool_pre_ping": True,
        "pool_recycle": 3600,
        "pool_size": 10,
        "max_overflow": 20,
    }


engine = create_engine(
    settings.database_url,
    echo=settings.environment == "development",
    future=True,
    **_engine_kwargs(settings.database_url),
)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Dependency for getting database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
