import asyncio
import threading
from datetime import datetime
from unittest.mock import Mock
import pandas as pd
import pytest
from pydantic import ValidationError

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.routes import backtest, price_alerts, scanner
from app.schemas.price_alert import PriceAlert
from app.schemas.scanner import Condition, ScannerRequest, ScannerResponse


def test_unknown_scanner_columns_are_rejected_before_running(monkeypatch):
    app = FastAPI()
    app.include_router(scanner.router)
    run = Mock()
    monkeypatch.setattr(scanner.to_process, 'run_sync', run)
    client = TestClient(app)
    response = client.post('/scanner/scan', json={'conditions': [{'column': 'rsi_typo', 'operator': 'gt', 'value': 70}]})
    assert response.status_code == 422
    assert client.post('/scanner/scan', json={'columns_to_return': ['missing']}).status_code == 422
    run.assert_not_called()


def test_scanner_dispatches_work_to_process(monkeypatch):
    calls = []
    async def run_sync(fn, request):
        calls.append((fn, request))
        return ScannerResponse(items=[], total=0)
    monkeypatch.setattr(scanner.to_process, 'run_sync', run_sync)
    app = FastAPI()
    app.include_router(scanner.router)
    response = TestClient(app).post('/scanner/scan', json={'conditions': [{'column': 'rsi_14', 'operator': 'gt', 'value': 70}]})
    assert response.status_code == 200 and calls[0][0] is scanner.scanner_service.scan


def test_backtest_dispatches_work_to_process(monkeypatch, memory_cache):
    calls = []
    async def run_sync(fn, *args):
        calls.append((fn, args))
        return {'open_trades': [], 'closed_trades': [], 'execution_time': {
            'total_seconds': 0, 'data_loading_seconds': 0, 'strategy_seconds': 0,
            'feature_building_seconds': 0, 'prediction_seconds': 0}}
    monkeypatch.setattr(backtest.to_process, 'run_sync', run_sync)
    app = FastAPI()
    app.include_router(backtest.router)
    response = TestClient(app).post('/backtest', json={'strategy': 'Dual RSI', 'start_date': '2025-01-01', 'symbols': ['TST']})
    assert response.status_code == 200
    assert calls[0] == (backtest.run_backtest, ('Dual RSI', '2025-01-01', ['TST'], True))


@pytest.mark.parametrize('operator,value', [
    ('gt', 'bogus'), ('gt', True), ('gt', {'x': 1}), ('gt', float('inf')),
    ('between', [10, 'bogus']), ('between', [20, 10]), ('in', ['bogus']),
    ('contains', '['),
])
def test_invalid_scanner_comparisons_are_rejected(operator, value):
    with pytest.raises(ValidationError):
        Condition(column='rsi_14', operator=operator, value=value)


def test_scanner_preserves_column_comparisons_and_normalizes_numeric_lists():
    frame = pd.DataFrame({'rsi_14': [50, 80], 'pi_upper': [60, 70]})
    request = ScannerRequest(conditions=[{'column': 'rsi_14', 'operator': 'gt', 'value': 'pi_upper'}])
    assert scanner.scanner_service._apply_conditions(frame, request)['rsi_14'].tolist() == [80]
    request = ScannerRequest(conditions=[{'column': 'rsi_14', 'operator': 'in', 'value': ['50']}])
    assert scanner.scanner_service._apply_conditions(frame, request)['rsi_14'].tolist() == [50]


def test_scanner_accepts_date_and_symbol_filters():
    frame = pd.DataFrame({'date': pd.to_datetime(['2025-01-01', '2025-02-01']), 'symbol': ['AAA', 'BBB']})
    request = ScannerRequest(conditions=[
        {'column': 'date', 'operator': 'between', 'value': ['2025-01-01', '2025-01-31']},
        {'column': 'symbol', 'operator': 'in', 'value': ['AAA']},
    ])
    assert scanner.scanner_service._apply_conditions(frame, request)['symbol'].tolist() == ['AAA']


def test_invalid_scanner_value_returns_422_without_worker(monkeypatch):
    run = Mock()
    monkeypatch.setattr(scanner.to_process, 'run_sync', run)
    app = FastAPI()
    app.include_router(scanner.router)
    response = TestClient(app).post('/scanner/scan', json={'conditions': [{'column': 'rsi_14', 'operator': 'gt', 'value': 'bogus'}]})
    assert response.status_code == 422
    run.assert_not_called()


@pytest.mark.parametrize('invalid_date', ['bad', '2025-02-30'])
def test_invalid_backtest_dates_are_422_before_worker(monkeypatch, invalid_date):
    run = Mock()
    monkeypatch.setattr(backtest.to_process, 'run_sync', run)
    app = FastAPI()
    app.include_router(backtest.router)
    client = TestClient(app)
    assert client.post('/backtest', json={'strategy': 'Dual RSI', 'start_date': invalid_date}).status_code == 422
    assert client.get('/backtest/plot', params={'start_date': invalid_date}).status_code == 422
    run.assert_not_called()


def test_alert_query_is_offloaded_and_quotes_are_deduplicated(monkeypatch):
    started, release = threading.Event(), threading.Event()
    now = datetime(2025, 1, 1)
    def alert(id, symbol):
        return PriceAlert(id=id, symbol=symbol, condition='gt', target_price=10, is_active=True,
                          is_triggered=False, created_at=now, updated_at=now)
    alerts = [alert(1, 'AAA'), alert(2, 'AAA'), alert(3, 'BBB')]
    def get_alerts(*args, **kwargs):
        started.set()
        release.wait(timeout=3)
        return alerts, 3
    monkeypatch.setattr(price_alerts.price_alert_service, 'get_alerts', get_alerts)
    calls = []
    async def run():
        both_started = asyncio.Event()
        async def price(ticker):
            calls.append(ticker)
            if len(calls) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)
            return 12
        monkeypatch.setattr(price_alerts, 'get_current_price', price)
        task = asyncio.create_task(price_alerts.list_alerts(symbol=None, is_active=None, is_triggered=None,
                                                           offset=0, limit=100, db=Mock()))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            assert not task.done()
        finally:
            release.set()
        result = await task
        assert result.total == 3
        assert [a.current_price for a in result.alerts] == [12, 12, 12]
        assert sorted(calls) == ['AAA', 'BBB']
    asyncio.run(run())
