import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.v1.routes import backtest, large_orders, regime, workflows
from app.core.settings import settings
from app.main import get_app
from app.schemas.regime import RegimeRequest
from app.services import prefect_workflow_service
from app.utils import wichart


def test_wichart_requires_configured_login(monkeypatch):
    monkeypatch.delenv('WICHART_EMAIL', raising=False)
    monkeypatch.delenv('WICHART_PASSWORD', raising=False)
    request = Mock()
    monkeypatch.setattr(wichart.requests, 'request', request)
    with pytest.raises(RuntimeError, match='WICHART_EMAIL and WICHART_PASSWORD'):
        wichart.getToken()
    request.assert_not_called()


def test_invalid_large_order_and_regime_dates_return_422():
    app = FastAPI()
    app.include_router(large_orders.router)
    app.include_router(regime.router)
    app.dependency_overrides[large_orders.get_large_orders_service] = lambda: Mock()
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get('/large-orders', params={'symbol': 'TST', 'from_date': 'bad'}).status_code == 422
    assert client.post('/regime/TST', json={'start_date': 'bad'}).status_code == 422
    with pytest.raises(ValidationError):
        RegimeRequest(start_date='bad')


def test_missing_h5_symbol_remains_404(monkeypatch):
    path = Mock()
    path.exists.return_value = True
    monkeypatch.setattr(backtest, 'H5_BACKTEST_FILE', path)
    monkeypatch.setattr(backtest.pd, 'read_hdf', Mock(side_effect=[pd.DataFrame({'symbol': []}), pd.DataFrame()]))
    app = FastAPI()
    app.include_router(backtest.router)
    assert TestClient(app).get('/backtest/h5/results?symbol=MISSING').status_code == 404


def test_workflow_returns_identifier_without_waiting(monkeypatch):
    job_id = uuid4()
    def submit(symbol):
        return SimpleNamespace(id=job_id, state='Scheduled')
    monkeypatch.setattr(workflows, 'run_sync_stock_workflow', submit)
    result = workflows.trigger_feature_store('TST')
    assert result.started and result.flow_run_id == str(job_id)
    dispatch = Mock(return_value=SimpleNamespace(id=job_id))
    monkeypatch.setattr(prefect_workflow_service, 'run_deployment', dispatch)
    prefect_workflow_service.run_sync_stock_workflow('TST')
    assert dispatch.call_args.kwargs['timeout'] == 0


def test_production_disables_openapi(monkeypatch):
    monkeypatch.setattr(settings, 'environment', 'production')
    app = get_app()
    assert app.openapi_url is None
    assert TestClient(app).get('/docs').status_code == 404
