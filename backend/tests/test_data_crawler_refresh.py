import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import BackgroundTasks


_MODULE_PATH = Path(__file__).parents[1] / "app/api/v1/routes/data_crawler.py"
_SPEC = importlib.util.spec_from_file_location("data_crawler_under_test", _MODULE_PATH)
assert _SPEC and _SPEC.loader
data_crawler = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(data_crawler)


def _db_with_existing_data():
    db = Mock()
    company = SimpleNamespace(company_id=7, ticker="HSG", name="Hoa Sen Group")
    db.query.return_value.filter.return_value.first.side_effect = [company, object()]
    return db


@pytest.mark.real_auth
@pytest.mark.asyncio
async def test_existing_symbol_is_skipped_without_refresh(monkeypatch):
    db = _db_with_existing_data()
    crawl = Mock()
    monkeypatch.setattr(data_crawler, "crawl_and_import_data", crawl)

    response = await data_crawler.crawl_symbol_data(
        "hsg", BackgroundTasks(), quarter=1, refresh=False, db=db
    )

    assert response["status"] == "skipped"
    crawl.assert_not_called()


@pytest.mark.real_auth
@pytest.mark.asyncio
async def test_refresh_existing_symbol_fetches_and_saves(monkeypatch):
    db = _db_with_existing_data()
    crawl = Mock()
    monkeypatch.setattr(data_crawler, "crawl_and_import_data", crawl)

    response = await data_crawler.crawl_symbol_data(
        "hsg", BackgroundTasks(), quarter=1, refresh=True, db=db
    )

    assert response["status"] == "completed"
    assert response["message"] == "Refreshed and saved latest financial data for HSG"
    crawl.assert_called_once_with("HSG", 1, db)
