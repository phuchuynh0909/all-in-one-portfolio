"""Shared stock universe used by backend jobs and endpoints."""
from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models.watchlist import SharedWatchlistSymbol


def load_shared_watchlist() -> list[str]:
    with SessionLocal() as session:
        symbols = list(session.scalars(
            select(SharedWatchlistSymbol.symbol).order_by(SharedWatchlistSymbol.position)
        ))
    if not symbols:
        raise ValueError("Shared watchlist is empty; populate shared_watchlist_symbols")
    return symbols
