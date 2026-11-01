from sqlalchemy import Column, ForeignKey, Integer, JSON, String

from app.db.base import Base


class WatchlistLibrary(Base):
    __tablename__ = "watchlist_libraries"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    lists = Column(JSON, nullable=False)
    active_list_id = Column(String(72), nullable=False)
    imported_ids = Column(JSON, nullable=False)


class SharedWatchlistSymbol(Base):
    __tablename__ = "shared_watchlist_symbols"

    symbol = Column(String(20), primary_key=True)
    position = Column(Integer, nullable=False, unique=True)
