from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from prefect.exceptions import ObjectNotFound
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.db.models.rag_submission import RagSubmission
from app.services import rag_submissions as submissions


@pytest.fixture
def db():
    engine = create_engine('sqlite://')
    RagSubmission.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_enqueue_is_idempotent_and_rejects_duplicate_active_jobs(db):
    key = uuid4()
    first = submissions.enqueue(db, key, 10, False, 'marker')
    assert submissions.enqueue(db, key, 10, False, 'marker').id == first.id
    with pytest.raises(ValueError, match='another request'):
        submissions.enqueue(db, key, 11, False, 'marker')
    with pytest.raises(ValueError, match='already queued'):
        submissions.enqueue(db, uuid4(), 10, False, 'marker')
    assert db.query(RagSubmission).count() == 1


def test_retry_uses_same_prefect_idempotency_key_and_reconciles(db, monkeypatch):
    key = uuid4()
    row = submissions.enqueue(db, key, 10, False, None)
    dispatch = Mock(side_effect=[TimeoutError('response lost'), SimpleNamespace(id=uuid4())])
    monkeypatch.setattr(submissions, 'run_rag_pipeline_deployment', dispatch)
    assert submissions.dispatch_next(db)
    assert row.state == 'queued'
    row.next_attempt_at = datetime.now() - timedelta(seconds=1)
    db.commit()
    assert submissions.dispatch_next(db)
    assert row.state == 'submitted'
    assert [call.kwargs['idempotency_key'] for call in dispatch.call_args_list] == [str(key), str(key)]
    state = Mock()
    state.is_final.return_value = True
    state.is_completed.return_value = False
    monkeypatch.setattr(submissions, 'get_rag_flow_run', lambda _: SimpleNamespace(state=state))
    row.next_attempt_at = datetime.now() - timedelta(seconds=1)
    db.commit()
    assert submissions.dispatch_next(db)
    assert row.state == 'failed' and row.active_report_id is None
    assert submissions.pending_statuses(db, [])[0]['status'] == 'FAILED'


def test_running_submission_uses_current_report_status(db):
    row = submissions.enqueue(db, uuid4(), 10, False, None)
    assert submissions.pending_statuses(db, [{'report_id': 10, 'status': 'EMBEDDED'}])[0]['status'] == 'PENDING'
    submissions.mark_running(db, row.id)
    assert row.state == 'queued'
    row.state = 'submitted'
    row.flow_run_id = str(uuid4())
    db.commit()
    submissions.mark_running(db, row.id)
    assert submissions.pending_statuses(db, [{'report_id': 10, 'status': 'PARSING'}])[0]['status'] == 'PARSING'


def test_deleted_prefect_run_releases_report_for_retry(db, monkeypatch):
    row = submissions.enqueue(db, uuid4(), 10, False, None)
    row.state = 'submitted'
    row.flow_run_id = str(uuid4())
    db.commit()
    monkeypatch.setattr(submissions, 'get_rag_flow_run', Mock(side_effect=ObjectNotFound(Exception('missing'))))
    assert submissions.dispatch_next(db)
    assert row.state == 'failed' and row.active_report_id is None
    assert 'no longer exists' in row.last_error
    assert submissions.enqueue(db, uuid4(), 10, False, None).state == 'queued'


def test_missing_deployment_is_retried(db, monkeypatch):
    row = submissions.enqueue(db, uuid4(), 10, False, None)
    monkeypatch.setattr(submissions, 'run_rag_pipeline_deployment', Mock(side_effect=ObjectNotFound(Exception('missing'))))
    assert submissions.dispatch_next(db)
    assert row.state == 'queued' and row.active_report_id == 10


def test_submission_migration_creates_and_removes_table():
    path = Path(__file__).resolve().parents[1] / 'alembic/versions/a27f6d13b408_add_rag_submissions.py'
    spec = spec_from_file_location('rag_submission_migration', path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert 'rag_submissions' in inspect(connection).get_table_names()
            migration.downgrade()
            assert 'rag_submissions' not in inspect(connection).get_table_names()
    engine.dispose()
