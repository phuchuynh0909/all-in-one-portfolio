"""Script CRUD and account isolation on existing MySQL, rolled back after each test."""
import pytest
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import require_user
from app.api.v1.routes.openscripts import router
from app.db.base import get_db
from app.db.models.openscript import OpenScript
from app.db.models.user import User

from tests.conftest import requires_mysql

pytestmark = [pytest.mark.real_auth, requires_mysql]
SOURCE = 'version 1\nstudy("Average")\nplot(close, "Close")'


@pytest.fixture
def library(db):
    # The shared db fixture nests service commits in a transaction and rolls back
    # the outer transaction, so no test users or sources remain in MySQL.
    users = [User(username=f"os-test-{uuid4().hex}", password_hash="unused") for _ in range(2)]
    db.add_all(users)
    db.commit()
    app = FastAPI()
    app.state.user_ids = [user.id for user in users]
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield client, app, db


def signed_in(app, user_id):
    app.dependency_overrides[require_user] = lambda: User(id=app.state.user_ids[user_id - 1])


def save(client, script_id="user_test", **changes):
    return client.put(f"/api/v1/openscripts/{script_id}", json={"title": "Average", "source": SOURCE, "visible": True, **changes})


def test_crud_survives_new_sessions_and_keeps_applied_source_separate(library):
    client, app, db = library
    signed_in(app, 1)
    assert save(client, applied_source=SOURCE).status_code == 200
    edited = SOURCE.replace("Average", "Edited")
    assert save(client, source=edited, applied_source=SOURCE).status_code == 200
    with Session(bind=db.connection(), join_transaction_mode="create_savepoint") as session:
        row = session.get(OpenScript, (app.state.user_ids[0], "user_test"))
        assert row.source == edited
        assert row.applied_source == SOURCE
    assert client.get("/api/v1/openscripts").json()[0]["source"] == edited
    assert client.delete("/api/v1/openscripts/user_test").status_code == 204
    assert client.get("/api/v1/openscripts").json() == []


def test_every_operation_is_scoped_to_the_signed_in_account(library):
    client, app, _ = library
    signed_in(app, 1)
    save(client)
    signed_in(app, 2)
    assert client.get("/api/v1/openscripts").json() == []
    assert client.delete("/api/v1/openscripts/user_test").status_code == 404
    assert client.patch("/api/v1/openscripts/visibility", json=[{"id": "user_test", "visible": False}]).status_code == 404
    assert save(client, title="Bob's script").status_code == 200
    signed_in(app, 1)
    assert client.get("/api/v1/openscripts").json()[0]["title"] == "Average"


def test_visibility_update_is_atomic_and_does_not_overwrite_source(library):
    client, app, _ = library
    signed_in(app, 1)
    save(client)
    response = client.patch("/api/v1/openscripts/visibility", json=[{"id": "user_test", "visible": False}, {"id": "user_missing", "visible": False}])
    assert response.status_code == 404
    assert client.get("/api/v1/openscripts").json()[0]["visible"] is True
    assert client.patch("/api/v1/openscripts/visibility", json=[{"id": "user_test", "visible": False}]).status_code == 204
    stored = client.get("/api/v1/openscripts").json()[0]
    assert stored["visible"] is False
    assert stored["source"] == SOURCE


def test_import_is_retryable_and_never_overwrites_server_edits(library):
    client, app, _ = library
    signed_in(app, 1)
    save(client)
    imported = [{"id": "user_test", "title": "Old browser copy", "source": "older"}, {"id": "user_other", "title": "Imported", "source": SOURCE}]
    for _ in range(2):
        assert client.post("/api/v1/openscripts/import", json=imported).status_code == 200
    scripts = {script["id"]: script for script in client.get("/api/v1/openscripts").json()}
    assert len(scripts) == 2
    assert scripts["user_test"]["source"] == SOURCE


def test_payload_limits_and_authentication(library):
    client, app, _ = library
    assert client.get("/api/v1/openscripts").status_code == 401
    signed_in(app, 1)
    assert save(client, source="").status_code == 422
    assert save(client, source="x" * 100_001).status_code == 422
    assert save(client, script_id="invalid").status_code == 422
    assert save(client, title="x" * 256).status_code == 422


def test_mysql_preserves_sources_larger_than_text_column_limit(library):
    client, app, _ = library
    signed_in(app, 1)
    source = "界" * 100_000
    assert save(client, source=source).status_code == 200
    assert client.get("/api/v1/openscripts").json()[0]["source"] == source
