"""Watchlist storage tests on existing MySQL; every write is rolled back."""
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import require_user
from app.api.v1.routes.watchlists import router
from app.db.base import get_db
from app.db.models.user import User
from app.db.models.watchlist import WatchlistLibrary
from tests.conftest import requires_mysql

pytestmark = [pytest.mark.real_auth, requires_mysql]
BASE = "/api/v1/watchlists"


@pytest.fixture
def library(db):
    users = [User(username=f"wl-test-{uuid4().hex}", password_hash="unused") for _ in range(2)]
    db.add_all(users)
    db.commit()
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    app.state.user_ids = [user.id for user in users]
    with TestClient(app) as client:
        yield client, app, db


def sign_in(app, index=0):
    app.dependency_overrides[require_user] = lambda: User(id=app.state.user_ids[index])


def payload(symbols=None):
    return {"lists": [{"id": "default", "title": "Watchlist", "symbols": symbols if symbols is not None else ["VNINDEX", "HPG"]}], "active_list_id": "default"}


def test_named_lists_order_sections_and_selection_survive_new_database_session(library):
    client, app, db = library
    sign_in(app)
    state = payload(["###Vietnam", " fpt ", "HPG", "FPT", "###Vietnam", "VCG"])
    state["lists"].append({"id": "research", "title": "Research", "symbols": []})
    state["active_list_id"] = "research"
    assert client.put(BASE, json=state).status_code == 200
    with Session(bind=db.connection(), join_transaction_mode="create_savepoint") as fresh:
        row = fresh.get(WatchlistLibrary, app.state.user_ids[0])
        assert row.active_list_id == "research"
        assert row.lists[0]["symbols"] == ["###Vietnam", "FPT", "HPG", "###Vietnam", "VCG"]
        assert row.lists[1] == state["lists"][1]
    assert client.get(BASE).json()["active_list_id"] == "research"


def test_accounts_have_independent_lists_and_active_selection(library):
    client, app, _ = library
    sign_in(app)
    client.put(BASE, json=payload(["HPG"]))
    sign_in(app, 1)
    assert client.get(BASE).json()["lists"] == []
    client.put(BASE, json=payload(["FPT"]))
    sign_in(app)
    assert client.get(BASE).json()["lists"][0]["symbols"] == ["HPG"]


def test_initialization_is_retryable_and_never_resets_existing_lists(library):
    client, app, _ = library
    sign_in(app)
    assert client.post(f"{BASE}/initialize", json=payload(["VCG"])).status_code == 200
    response = client.post(f"{BASE}/initialize", json=payload(["HPG"]))
    assert response.json()["lists"][0]["symbols"] == ["VCG"]


def test_rename_delete_and_empty_lists_persist(library):
    client, app, _ = library
    sign_in(app)
    state = payload()
    state["lists"].append({"id": "second", "title": "Second", "symbols": ["FPT"]})
    state["active_list_id"] = "second"
    client.put(BASE, json=state)
    changed = {"lists": [{"id": "second", "title": "Renamed", "symbols": []}], "active_list_id": "second"}
    assert client.put(BASE, json=changed).status_code == 200
    response = client.get(BASE).json()
    assert response["lists"] == changed["lists"]
    assert response["active_list_id"] == "second"


def test_import_keeps_server_data_handles_id_collisions_and_preserves_server_edits(library):
    client, app, _ = library
    sign_in(app)
    client.put(BASE, json=payload(["HPG"]))
    original = payload(["FPT", "###Research", "SHS"])
    imported = client.post(f"{BASE}/import", json=original).json()
    assert len(imported["lists"]) == 2
    assert imported["active_list_id"].startswith("browser_")
    assert imported["lists"][0]["symbols"] == ["HPG"]
    imported["lists"][1]["symbols"] = ["VNM"]
    client.put(BASE, json={"lists": imported["lists"], "active_list_id": imported["active_list_id"]})
    repeated = client.post(f"{BASE}/import", json=original).json()
    assert len(repeated["lists"]) == 2
    assert repeated["lists"][1]["symbols"] == ["VNM"]
    assert repeated["imported_ids"] == ["default"]


def test_invalid_save_does_not_replace_a_valid_library(library):
    client, app, _ = library
    sign_in(app)
    client.put(BASE, json=payload())
    invalid = [
        {"lists": [], "active_list_id": "default"},
        {**payload(), "active_list_id": "missing"},
        {"lists": payload()["lists"] * 2, "active_list_id": "default"},
        {"lists": [{"id": "default", "title": " ", "symbols": []}], "active_list_id": "default"},
        payload(["x" * 121]),
    ]
    for state in invalid:
        assert client.put(BASE, json=state).status_code == 422
    assert client.get(BASE).json()["lists"] == payload()["lists"]


def test_api_requires_login(library):
    client, _, _ = library
    assert client.get(BASE).status_code == 401
    assert client.put(BASE, json=payload()).status_code == 401
