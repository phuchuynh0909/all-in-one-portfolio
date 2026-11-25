import asyncio
import inspect
import json
import sys
import types
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.v1.routes import isp_alerts, report
from app.db.models.market import Sector
from app.services import corporate_action_service, portfolio_service, sector_service, stock_service
from app.services import report_rag_service


def test_sector_import_upserts_and_rolls_back_invalid_file(tmp_path):
    engine = create_engine('sqlite://')
    Sector.__table__.create(engine)
    path = tmp_path / 'sectors.json'
    with Session(engine) as db:
        path.write_text(json.dumps([{'id': 1, 'data': [{'key': 'name', 'value': 'Old'}]}]))
        assert sector_service.upsert_sector_from_file(db, str(path), 2) == 1
        path.write_text(json.dumps([{'id': 1, 'data': [{'key': 'name', 'value': 'New'}]}]))
        assert sector_service.upsert_sector_from_file(db, str(path), 2) == 1
        assert db.get(Sector, (1, 2)).name == 'New'
        path.write_text(json.dumps([{'id': 1, 'data': [{'key': 'name', 'value': 'Wrong'}]}, {'id': 'invalid'}]))
        with pytest.raises(ValueError):
            sector_service.upsert_sector_from_file(db, str(path), 2)
        assert db.get(Sector, (1, 2)).name == 'New'


def test_unapply_checks_locked_current_position(monkeypatch):
    action = SimpleNamespace(id=1, status='applied', action_type='cash')
    record = SimpleNamespace(position_id=3, qty_before=Decimal(100), qty_after=Decimal(110), price_before=Decimal(10), price_after=Decimal(9))
    changed_position = SimpleNamespace(quantity=Decimal(60), purchase_price=Decimal(9))
    db = MagicMock()
    db.execute.return_value.scalars.return_value.all.return_value = [record]
    query = db.query.return_value.filter.return_value.populate_existing.return_value.with_for_update.return_value
    query.first.return_value = changed_position
    monkeypatch.setattr(corporate_action_service, '_lock_symbol_actions', lambda *_: (action, [action]))
    with pytest.raises(ValueError, match='now holds 60'):
        corporate_action_service.unapply_action(db, 1)
    query.first.assert_called_once()
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_missing_quote_is_not_reported_as_zero_profit(monkeypatch):
    position = SimpleNamespace(ticker='AAA', purchase_price=Decimal(10), quantity=Decimal(2))
    db = MagicMock()
    db.query.return_value.order_by.return_value.all.return_value = [position]
    async def unavailable(_, **kwargs):
        raise RuntimeError('quote unavailable')
    monkeypatch.setattr(portfolio_service, 'get_current_price', unavailable)
    monkeypatch.setattr(portfolio_service, '_calculate_realized_pl', lambda _: Decimal(0))
    monkeypatch.setattr(portfolio_service, '_calculate_dividend_income', lambda _: (Decimal(0), Decimal(0)))
    # The schema accepts ORM-like positions; supply its required attributes.
    position.id = 1
    position.purchase_date = date(2025, 1, 1)
    position.created_at = __import__('datetime').datetime(2025, 1, 1)
    position.notes = None
    summary = asyncio.run(portfolio_service.get_portfolio_summary(db))
    assert summary.positions[0].current_price is None
    assert summary.total_value is None
    assert summary.total_profit_loss is None
    assert summary.total_profit_loss_pct is None
    assert summary.total_invested == Decimal(20)


def test_failed_quote_is_retried(monkeypatch, memory_cache):
    calls = []
    client = MagicMock()
    client.query.return_value.result_rows = [(12.0,)]

    def connect():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('temporary outage')
        return client

    monkeypatch.setattr(stock_service, '_clickhouse_client', connect)

    async def run():
        with pytest.raises(RuntimeError, match='temporary outage'):
            await stock_service.get_current_price('AAA')
        assert await stock_service.get_current_price('AAA') == 12.0
        assert len(calls) == 2

    asyncio.run(run())


def test_rag_request_records_submission_before_dispatch():
    from uuid import uuid4
    from app.db.models.rag_submission import RagSubmission
    engine = create_engine('sqlite://')
    RagSubmission.__table__.create(engine)
    with Session(engine) as db:
        key = uuid4()
        assert report.trigger_rag(12, idempotency_key=key, db=db)['status'] == 'PENDING'
        assert db.get(RagSubmission, str(key)).state == 'queued'
        assert report.trigger_rag(12, idempotency_key=key, db=db)['submission_id'] == str(key)
    engine.dispose()


def test_isp_handlers_run_in_worker_threads():
    for handler in (isp_alerts.get_isp_alerts, isp_alerts.get_latest_alerts, isp_alerts.get_active_symbols, isp_alerts.get_alert_statistics):
        assert not inspect.iscoroutinefunction(handler)
