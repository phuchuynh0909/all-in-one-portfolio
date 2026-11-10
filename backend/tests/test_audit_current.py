from datetime import date
from decimal import Decimal
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.db.models.portfolio import Position, Transaction
from app.db.models.corporate_action import CorporateAction, CorporateActionApplication
from app.schemas.portfolio import ClosePositionRequest, PositionCreate, TransactionCreate
from app.services import portfolio_service
from app.services.financial_data_importer import FinancialDataImporter
from app.db.base import get_db
from app.main import app


def test_statement_cache_is_isolated_and_cleared_after_rollback():
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = []
    importer = FinancialDataImporter(db)
    importer.get_company_by_ticker = MagicMock(return_value=1)
    importer.insert_periods = MagicMock(return_value={})
    importer._upsert_returning_id = MagicMock(side_effect=[42, 99])
    importer.insert_statement_items = MagicMock(side_effect=RuntimeError('failed import'))
    payload = {'time': [], 'candoiketoan': [{'key': 'value'}]}
    with pytest.raises(RuntimeError, match='failed import'):
        importer.import_financial_data(payload, 'TST')
    db.rollback.assert_called_once()
    assert importer.get_or_create_statements(payload)['candoiketoan'] == 99
    other = FinancialDataImporter(db)
    other._upsert_returning_id = MagicMock(return_value=100)
    assert other.get_or_create_statements(payload)['candoiketoan'] == 100


def test_manual_transactions_can_be_edited_but_position_closes_are_protected():
    engine = create_engine('sqlite://')
    Position.__table__.create(engine)
    Transaction.__table__.create(engine)
    CorporateAction.__table__.create(engine)
    CorporateActionApplication.__table__.create(engine)
    with Session(engine) as db:
        position = portfolio_service.create_position(db, PositionCreate(ticker='TST', quantity=10, purchase_price=10, purchase_date=date(2025, 1, 1)))
        closed = portfolio_service.close_position(db, ClosePositionRequest(position_id=position.id, quantity_to_close=2, closing_price=15, closing_date=date(2025, 2, 1)))
        data = TransactionCreate(ticker='TST', transaction_type='buy', quantity=1, price=10, transaction_date=date(2025, 2, 1))
        manual = portfolio_service.create_transaction(db, data)
        updated = portfolio_service.update_transaction(db, manual.id, data.model_copy(update={'price': Decimal(11)}))
        assert updated.price == 11
        assert portfolio_service.delete_transaction(db, manual.id)
        with pytest.raises(ValueError, match='cannot be edited or deleted'):
            portfolio_service.delete_transaction(db, closed.transaction_id)
        with pytest.raises(ValueError, match='cannot be edited or deleted'):
            portfolio_service.update_transaction(db, closed.transaction_id, data)
        assert db.get(Position, position.id).quantity == 8
        assert db.get(Transaction, closed.transaction_id).is_position_close
    engine.dispose()


def test_migration_conservatively_protects_existing_sells():
    path = Path(__file__).resolve().parents[1] / 'alembic/versions/f1c90b8a42d3_protect_position_close_transactions.py'
    spec = spec_from_file_location('protect_transaction_migration', path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE transactions (id INTEGER PRIMARY KEY, transaction_type VARCHAR(32) NOT NULL)'))
        conn.execute(text("INSERT INTO transactions VALUES (1, 'sell'), (2, 'buy'), (3, 'dividend_cash')"))
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        rows = conn.execute(text('SELECT id, is_position_close FROM transactions ORDER BY id')).all()
        assert rows == [(1, 1), (2, 0), (3, 0)]
    engine.dispose()


def test_transaction_api_updates_manual_and_rejects_protected():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Position.__table__.create(engine)
    Transaction.__table__.create(engine)
    CorporateAction.__table__.create(engine)
    CorporateActionApplication.__table__.create(engine)
    with Session(engine) as db:
        position = portfolio_service.create_position(db, PositionCreate(ticker='TST', quantity=10, purchase_price=10, purchase_date=date(2025, 1, 1)))
        close = portfolio_service.close_position(db, ClosePositionRequest(position_id=position.id, quantity_to_close=2, closing_price=15, closing_date=date(2025, 2, 1)))

    def session():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = session
    try:
        with TestClient(app) as client:
            payload = {'ticker': 'TST', 'transaction_type': 'buy', 'quantity': 1, 'price': 10, 'transaction_date': '2025-01-01'}
            created = client.post('/api/v1/portfolio/transactions', json=payload)
            assert created.status_code == 200
            manual_id = created.json()['id']
            payload['price'] = 11
            updated = client.put(f'/api/v1/portfolio/transactions/{manual_id}', json=payload)
            assert updated.status_code == 200 and Decimal(updated.json()['price']) == 11
            assert client.delete(f'/api/v1/portfolio/transactions/{manual_id}').status_code == 200
            assert client.put(f'/api/v1/portfolio/transactions/{close.transaction_id}', json=payload).status_code == 409
            assert client.delete(f'/api/v1/portfolio/transactions/{close.transaction_id}').status_code == 409
            assert client.put('/api/v1/portfolio/transactions/99999', json=payload).status_code == 404
            for kind in ('dividend_cash', 'dividend_stock'):
                payload['transaction_type'] = kind
                response = client.post('/api/v1/portfolio/transactions', json=payload)
                assert response.status_code == 400
                assert 'corporate actions' in response.json()['detail']
            with Session(engine) as db:
                assert db.query(Transaction).count() == 1
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_position_update_conflicts_and_trade_validation():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Position.__table__.create(engine)
    Transaction.__table__.create(engine)
    CorporateAction.__table__.create(engine)
    CorporateActionApplication.__table__.create(engine)

    def session():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = session
    try:
        with TestClient(app) as client:
            original = {'ticker': 'TST', 'quantity': '100', 'purchase_price': '14.054545',
                        'purchase_date': '2025-01-01', 'notes': None}
            created = client.post('/api/v1/portfolio/positions', json=original)
            assert created.status_code == 200
            position_id = created.json()['id']
            payload = {**original, 'notes': 'updated', 'original': original}
            saved = client.put(f'/api/v1/portfolio/positions/{position_id}', json=payload)
            assert saved.status_code == 200
            assert Decimal(saved.json()['purchase_price']) == Decimal('14.054545')
            assert client.put(f'/api/v1/portfolio/positions/{position_id}', json=payload).status_code == 409
            assert client.put(f'/api/v1/portfolio/positions/{position_id}', json=original).status_code == 422

            invalid_close = {'position_id': position_id, 'quantity_to_close': 40,
                             'closing_price': 20, 'closing_date': '2024-12-31'}
            assert client.post('/api/v1/portfolio/positions/close', json=invalid_close).status_code == 400
            invalid_sell = {'ticker': 'TST', 'transaction_type': 'sell', 'quantity': 1,
                            'price': 10, 'close_price': -1, 'transaction_date': '2025-01-01'}
            assert client.post('/api/v1/portfolio/transactions', json=invalid_sell).status_code == 422
            invalid_close['closing_date'] = '2025-02-01'
            assert client.post('/api/v1/portfolio/positions/close', json=invalid_close).status_code == 200
            payload['original'] = {**original, 'notes': 'updated'}
            assert client.put(f'/api/v1/portfolio/positions/{position_id}', json=payload).status_code == 409
            with Session(engine) as db:
                assert db.get(Position, position_id).quantity == 60
                assert db.query(Transaction).count() == 1
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
