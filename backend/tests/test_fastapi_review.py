import asyncio
import threading
from datetime import date
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from app.api.v1.routes import data_crawler, portfolio, trade_flow
from app.schemas.portfolio import ClosePositionRequest, PositionCreate, TransactionCreate
from app.services import portfolio_service


@pytest.mark.parametrize('model,payload', [
    (PositionCreate, dict(ticker='TST', quantity='0.0000001', purchase_price=10, purchase_date='2025-01-01')),
    (TransactionCreate, dict(ticker='TST', transaction_type='sell', quantity=1, price=10, close_price='0.0000001', transaction_date='2025-01-01')),
    (ClosePositionRequest, dict(position_id=1, quantity_to_close=1, closing_price=10, closing_date='2025-01-01', fees='0.001')),
])
def test_money_rejects_values_database_would_round(model, payload):
    with pytest.raises(ValidationError):
        model(**payload)


def test_portfolio_query_does_not_block_event_loop():
    started, release = threading.Event(), threading.Event()
    db = Mock()
    def query():
        started.set()
        release.wait(timeout=3)
        return []
    db.query.return_value.order_by.return_value.all.side_effect = query
    async def run():
        task = asyncio.create_task(portfolio_service.get_positions(db))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            assert not task.done()
        finally:
            release.set()
            assert await task == []
    asyncio.run(run())


def test_invalid_trade_flow_date_is_422():
    app = FastAPI()
    app.include_router(trade_flow.router)
    app.dependency_overrides[trade_flow.get_trade_flow_service] = lambda: Mock()
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get('/trade-flow/anomalies', params={'symbol': 'TST', 'from_date': 'invalid'})
            assert response.status_code == 422
    asyncio.run(run())


def test_crawler_uses_timeout_and_rolls_back(monkeypatch):
    monkeypatch.setattr(data_crawler, 'getToken', lambda: 'token')
    monkeypatch.setattr(data_crawler, 'getHeaders', lambda *args: {})
    get = Mock(side_effect=data_crawler.requests.Timeout('timeout'))
    monkeypatch.setattr(data_crawler.requests, 'get', get)
    db = Mock()
    with pytest.raises(data_crawler.requests.Timeout):
        data_crawler.crawl_and_import_data('TST', 1, db)
    assert get.call_args.kwargs['timeout'] == 30
    db.rollback.assert_called_once()


def test_unexpected_position_error_is_not_exposed(monkeypatch):
    monkeypatch.setattr(portfolio_service, 'close_position', Mock(side_effect=RuntimeError('private database detail')))
    request = ClosePositionRequest(position_id=1, quantity_to_close=1, closing_price=10, closing_date=date(2025, 1, 1))
    with pytest.raises(HTTPException) as error:
        portfolio.close_position(request, db=Mock())
    assert error.value.status_code == 500
    assert 'private' not in error.value.detail
