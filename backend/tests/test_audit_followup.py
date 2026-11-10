from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
import asyncio
import json
import subprocess
import sys
import threading
import time

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.dialects import mysql

from app.db.models.portfolio import Position, Transaction
from app.db.models.corporate_action import CorporateAction, CorporateActionApplication
from app.db.models.market import Sector
from app.services import corporate_action_service as actions, portfolio_service, sector_service, stock_service
from app.schemas.portfolio import ClosePositionRequest, PositionCreate, PositionUpdate
from app.main import app


@pytest.fixture
def book():
    engine = create_engine('sqlite://')
    for model in (Position, Transaction, CorporateAction, CorporateActionApplication):
        model.__table__.create(engine)
    with Session(engine) as db:
        lot = Position(ticker='TST', quantity=100, purchase_price=10, purchase_date=date(2025, 1, 1))
        db.add(lot)
        db.commit()
        yield db, lot
    engine.dispose()


def event(db, event_id, ex_date, kind='stock', ratio='0.1'):
    row = CorporateAction(symbol='TST', event_id=event_id, name=kind, action_type=kind,
                          ex_date=ex_date, ratio=Decimal(ratio) if kind == 'stock' else None,
                          amount_per_share=Decimal(1000) if kind == 'cash' else None,
                          title=kind, source='manual', status='pending')
    db.add(row)
    db.commit()
    return row


@pytest.mark.parametrize('kind', ['cash', 'stock'])
@pytest.mark.parametrize('quantity', [40, 100])
def test_dividend_after_sale_rejects_uncertain_historical_holding(book, kind, quantity):
    db, lot = book
    action = event(db, 1, date(2026, 1, 1), kind=kind)
    portfolio_service.close_position(db, ClosePositionRequest(
        position_id=lot.id, quantity_to_close=quantity, closing_price=12,
        closing_date=date(2026, 1, 2)))
    with pytest.raises(ValueError, match='historical holding uncertain'):
        actions.apply_action(db, action.id)
    assert db.get(CorporateAction, action.id).status == 'pending'
    assert db.query(CorporateActionApplication).count() == 0


def test_dividend_allows_sale_before_ex_date(book):
    db, lot = book
    action = event(db, 1, date(2026, 1, 2), kind='cash')
    portfolio_service.close_position(db, ClosePositionRequest(
        position_id=lot.id, quantity_to_close=40, closing_price=12,
        closing_date=date(2026, 1, 1)))
    result = actions.apply_action(db, action.id)
    assert result.lots[0].qty_before == 60


def test_dividend_rejects_same_day_manual_sale_case_insensitively(book):
    db, _ = book
    action = event(db, 1, date(2026, 1, 1), kind='cash')
    db.add(Transaction(ticker='tst', transaction_type='sell', quantity=1, price=10,
                       transaction_date=date(2026, 1, 1)))
    db.commit()
    with pytest.raises(ValueError, match='historical holding uncertain'):
        actions.apply_action(db, action.id)


def test_applied_action_rejects_backdated_sale_and_historical_changes(book):
    db, lot = book
    action = event(db, 1, date(2026, 1, 1), kind='cash')
    actions.apply_action(db, action.id)
    with pytest.raises(ValueError, match='Reverse applied'):
        portfolio_service.close_position(db, ClosePositionRequest(
            position_id=lot.id, quantity_to_close=40, closing_price=12,
            closing_date=date(2025, 12, 1)))
    original = PositionCreate.model_validate(lot, from_attributes=True)
    with pytest.raises(ValueError, match='Reverse applied'):
        portfolio_service.update_position(db, lot.id, PositionUpdate(**original.model_dump(), original=original).model_copy(update={'quantity': Decimal(60)}))
    with pytest.raises(ValueError, match='Reverse applied'):
        portfolio_service.create_position(db, original)
    updated = portfolio_service.update_position(db, lot.id, PositionUpdate(**{**original.model_dump(), 'notes': 'corrected'}, original=original))
    assert updated.notes == 'corrected'
    assert db.query(CorporateActionApplication).one().qty_before == 100


def test_future_action_is_not_applied(book):
    db, lot = book
    action = event(db, 1, date.today() + timedelta(days=3), kind='stock')
    with pytest.raises(ValueError, match='before its ex-date'):
        actions.apply_action(db, action.id)
    assert action.status == 'pending'
    assert lot.quantity == 100


def test_sync_includes_closed_symbols(book, monkeypatch):
    db, lot = book
    portfolio_service.close_position(db, ClosePositionRequest(
        position_id=lot.id, quantity_to_close=100, closing_price=12,
        closing_date=date(2026, 1, 1)))
    symbols = []
    def sync_symbol(db, symbol, *, session=None):
        symbols.append(symbol)
        return {'inserted': 0, 'skipped': 0, 'unparsed': 0, 'ignored': 0}
    monkeypatch.setattr(actions, 'sync_symbol', sync_symbol)
    actions.sync_all(db)
    assert symbols == ['TST']


def test_model_imports_do_not_depend_on_import_order():
    for name in ('portfolio', 'corporate_action', 'user'):
        subprocess.run([sys.executable, '-c', f'from app.db.models.{name} import {dict(portfolio="Position", corporate_action="CorporateAction", user="User")[name]}'], check=True)


def test_unapply_reverses_same_day_stock_group_only(book):
    db, lot = book
    first = event(db, 1, date(2026, 1, 1))
    second = event(db, 2, date(2026, 1, 1), ratio='0.2')
    cash = event(db, 3, date(2026, 1, 1), kind='cash')
    actions.apply_action(db, first.id)
    assert lot.quantity == 130
    result = actions.unapply_action(db, first.id)
    assert lot.quantity == 100
    assert first.status == second.status == 'pending'
    assert cash.status == 'applied'
    assert set(result.unapplied_action_ids) == {first.id, second.id}
    assert sorted(lot.shares_added for lot in result.lots) == [Decimal('-30'), Decimal('0')]
    assert result.total_shares_added == Decimal('-30')


def test_unapply_rejects_later_applied_event(book):
    db, lot = book
    first = event(db, 1, date(2026, 1, 1))
    later = event(db, 2, date(2026, 2, 1), kind='cash')
    actions.apply_action(db, first.id)
    actions.apply_action(db, later.id)
    with pytest.raises(ValueError, match='later applied event'):
        actions.unapply_action(db, first.id)
    assert lot.quantity == 110
    assert first.status == later.status == 'applied'


@pytest.mark.parametrize('change', ['price', 'closed'])
def test_unapply_rejects_modified_or_closed_lot(book, change):
    db, lot = book
    first = event(db, 1, date(2026, 1, 1))
    actions.apply_action(db, first.id)
    if change == 'price':
        lot.purchase_price = Decimal(7)
    else:
        db.delete(lot)
    db.commit()
    with pytest.raises(ValueError):
        actions.unapply_action(db, first.id)
    assert first.status == 'applied'
    if change == 'price':
        assert lot.purchase_price == 7


@pytest.mark.parametrize('bad_entry', [None, {}, {'id': 2, 'data': 'bad'}, {'id': 2, 'data': [{'key': 'name'}]}])
def test_sector_import_rejects_invalid_entry_and_rolls_back(tmp_path, bad_entry):
    engine = create_engine('sqlite://')
    Sector.__table__.create(engine)
    path = tmp_path / 'sectors.json'
    path.write_text(json.dumps([{'id': 1, 'data': [{'key': 'name', 'value': 'ok'}]}, bad_entry]))
    with Session(engine) as db:
        with pytest.raises(ValueError):
            sector_service.upsert_sector_from_file(db, str(path), 1)
        assert db.get(Sector, (1, 1)) is None
    engine.dispose()


def test_current_price_can_include_closing_date(monkeypatch, memory_cache):
    client = MagicMock()
    client.query.return_value.result_rows = [(12.5, date(2025, 1, 3))]
    monkeypatch.setattr(stock_service, '_clickhouse_client', lambda: client)
    quote = asyncio.run(stock_service.get_current_price('TST', include_date=True))
    assert quote == {'price': 12.5, 'date': '2025-01-03'}


def test_sector_metrics_cannot_change_identifier_or_level(tmp_path):
    engine = create_engine('sqlite://')
    Sector.__table__.create(engine)
    path = tmp_path / 'sectors.json'
    path.write_text(json.dumps([{'id': 1, 'data': [{'key': 'id', 'value': 99}, {'key': 'level', 'value': 3}, {'key': 'name', 'value': 'Updated'}]}]))
    with Session(engine) as db:
        db.add(Sector(id=1, level=2, name='Original'))
        db.commit()
        assert sector_service.upsert_sector_from_file(db, str(path), 2) == 1
        assert db.get(Sector, (1, 2)).name == 'Updated'
        assert db.get(Sector, (1, 3)) is None
        assert db.get(Sector, (99, 2)) is None
    engine.dispose()


def test_symbol_lock_refreshes_rows_using_mysql_locking_read(monkeypatch):
    stale = MagicMock(id=1, symbol='TST')
    fresh = MagicMock(id=1, symbol='TST', status='applied')
    db = MagicMock()
    db.execute.return_value.scalars.return_value.all.return_value = [fresh]
    monkeypatch.setattr(actions, '_load', lambda *_: stale)
    action, rows = actions._lock_symbol_actions(db, 1)
    statement = db.execute.call_args.args[0]
    assert 'FOR UPDATE' in str(statement.compile(dialect=mysql.dialect()))
    assert statement.get_execution_options()['populate_existing'] is True
    assert action is fresh and rows == [fresh]


@pytest.mark.asyncio
async def test_sector_import_does_not_block_other_requests(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def slow_import(*args):
        started.set()
        release.wait(3)
        return 0

    monkeypatch.setattr(sector_service, 'upsert_sector_from_file', slow_import)
    timer = threading.Timer(3, release.set)
    timer.start()
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            upload = asyncio.create_task(client.post('/api/v1/sector/import/1', files={'file': ('sectors.json', b'[]')}))
            assert await asyncio.to_thread(started.wait, 2)
            response = await client.get('/api/v1/health')
            assert response.status_code == 200
            assert time.monotonic() - start < 2
            release.set()
            assert (await upload).status_code == 200
    finally:
        release.set()
        timer.cancel()
