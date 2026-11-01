import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker

from app.db.models.watchlist import SharedWatchlistSymbol
from app.services import shared_watchlist

pytestmark = pytest.mark.real_auth


def test_migration_seeds_ordered_watchlist_and_reader_rejects_empty(monkeypatch):
    engine = create_engine("sqlite://")
    path = Path(__file__).resolve().parents[1] / "alembic/versions/ed24f8c93a10_add_shared_watchlist.py"
    spec = importlib.util.spec_from_file_location("shared_watchlist_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.upgrade()
    monkeypatch.setattr(shared_watchlist, "SessionLocal", sessionmaker(bind=engine))
    expected = list(migration.SYMBOLS)
    assert shared_watchlist.load_shared_watchlist() == expected
    assert len(expected) == len(set(expected))
    with engine.begin() as connection:
        connection.execute(delete(SharedWatchlistSymbol))
    with pytest.raises(ValueError, match="empty"):
        shared_watchlist.load_shared_watchlist()
