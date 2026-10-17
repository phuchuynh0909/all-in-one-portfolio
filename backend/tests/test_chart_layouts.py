"""MySQL layout/indicator persistence, with all test writes rolled back."""
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import require_user
from app.api.v1.routes.chart_layouts import router
from app.db.base import get_db
from app.db.models.chart_layout import ChartLayout
from app.db.models.openscript import OpenScript
from app.db.models.user import User
from tests.conftest import requires_mysql

pytestmark = [pytest.mark.real_auth, requires_mysql]
BASE = "/api/v1/chart-layouts"


@pytest.fixture
def library(db):
    users = [User(username=f"layout-test-{uuid4().hex}", password_hash="unused") for _ in range(2)]
    db.add_all(users)
    db.commit()
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    app.state.user_ids = [user.id for user in users]
    db.add(OpenScript(user_id=users[0].id, id="user_indicator", title="Oscillator", source="example", visible=False))
    db.commit()
    with TestClient(app) as client:
        yield client, app, db


def sign_in(app, index=0):
    app.dependency_overrides[require_user] = lambda: User(id=app.state.user_ids[index])


def save(client, layout_id="custom_research", **changes):
    return client.put(f"{BASE}/{layout_id}", json={
        "name": "Research", "indicators": ["rsi", "kama"],
        "params": {"rsi": {"period": 21}, "kama": {"timeperiod": 30}},
        "scripts": ["user_indicator"], **changes,
    })


def test_layout_membership_settings_and_native_chart_state_survive_new_session(library):
    client, app, db = library
    sign_in(app)
    snapshot = {"charts": [{"panes": [{"sources": [{"id": "study1", "state": {
        "inputs": {"length": 55}, "styles": {"plot": {"color": "#00ffaa"}},
    }}]}]}]}
    assert save(client, chart_state=snapshot).status_code == 200
    with Session(bind=db.connection(), join_transaction_mode="create_savepoint") as fresh:
        layout = fresh.get(ChartLayout, (app.state.user_ids[0], "custom_research"))
        assert layout.indicators == ["rsi", "kama"]
        assert layout.params["rsi"]["period"] == 21
        assert layout.scripts == ["user_indicator"]
        assert layout.chart_state == snapshot
    assert client.get(BASE).json()["layouts"][0]["chart_state"] == snapshot


def test_selection_restores_each_layouts_script_membership_and_is_persistent(library):
    client, app, db = library
    sign_in(app)
    save(client)
    save(client, "custom_price", name="Price", scripts=[], indicators=[], params={})
    for layout_id, expected_visible in [("custom_research", True), ("custom_price", False)]:
        response = client.post(f"{BASE}/active", json={"layout_id": layout_id})
        assert response.status_code == 200
        assert client.get(BASE).json()["active_layout_id"] == layout_id
        db.expire_all()
        assert db.get(OpenScript, (app.state.user_ids[0], "user_indicator")).visible is expected_visible
    layouts = {row["id"]: row for row in client.get(BASE).json()["layouts"]}
    assert layouts["custom_research"]["params"]["rsi"]["period"] == 21
    assert layouts["custom_price"]["indicators"] == []


def test_layouts_and_selection_are_private_to_each_account(library):
    client, app, _ = library
    sign_in(app)
    save(client)
    sign_in(app, 1)
    assert client.get(BASE).json() == {"layouts": [], "active_layout_id": None}
    assert client.post(f"{BASE}/active", json={"layout_id": "custom_research"}).status_code == 404
    assert save(client).status_code == 404  # The script belongs to the other account.
    assert save(client, scripts=[], name="Other account").status_code == 200
    sign_in(app)
    assert client.get(BASE).json()["layouts"][0]["name"] == "Research"


def test_import_retries_preserve_server_edits_and_selected_layout(library):
    client, app, _ = library
    sign_in(app)
    layout = {"id": "custom_old", "name": "Old setup", "indicators": ["rsi"], "params": {"rsi": {"period": 14}}, "scripts": ["user_indicator", "user_deleted"]}
    payload = {"layouts": [layout], "active_layout_id": layout["id"]}
    assert client.post(f"{BASE}/import", json=payload).status_code == 200
    assert save(client, layout["id"], name="Edited", params={"rsi": {"period": 40}}).status_code == 200
    response = client.post(f"{BASE}/import", json=payload)
    assert response.status_code == 200
    assert response.json()["active_layout_id"] == layout["id"]
    assert response.json()["layouts"][0]["params"]["rsi"]["period"] == 40
    assert response.json()["layouts"][0]["scripts"] == ["user_indicator"]


def test_invalid_or_oversized_updates_leave_saved_layout_unchanged(library):
    client, app, _ = library
    sign_in(app)
    save(client)
    assert save(client, params={"rsi": {"period": "bad"}}).status_code == 422
    assert save(client, chart_state={"data": "x" * 2_000_000}).status_code == 422
    assert save(client, name="   ").status_code == 422
    assert save(client, "invalid").status_code == 422
    assert save(client, "custom_other").status_code == 409
    assert client.get(BASE).json()["layouts"][0]["params"]["rsi"]["period"] == 21


def test_layout_api_requires_login(library):
    client, _, _ = library
    assert client.get(BASE).status_code == 401
    assert save(client).status_code == 401
    assert client.patch(f"{BASE}/custom_research", json={"name": "New name"}).status_code == 401
    assert client.delete(f"{BASE}/custom_research").status_code == 401


def test_rename_persists_without_changing_indicator_setup(library):
    client, app, db = library
    sign_in(app)
    snapshot = {"charts": [{"panes": [{"height": 240}]}]}
    before = save(client, chart_state=snapshot).json()
    client.post(f"{BASE}/active", json={"layout_id": before["id"]})
    response = client.patch(f"{BASE}/{before['id']}", json={"name": "  Momentum  "})
    assert response.status_code == 200
    assert response.json() == {**before, "name": "Momentum"}
    with Session(bind=db.connection(), join_transaction_mode="create_savepoint") as fresh:
        assert fresh.get(ChartLayout, (app.state.user_ids[0], before["id"])).name == "Momentum"
    assert client.get(BASE).json()["active_layout_id"] == before["id"]


def test_rename_rejects_invalid_duplicate_names_and_builtin_layouts(library):
    client, app, _ = library
    sign_in(app)
    save(client)
    save(client, "custom_other", name="Other")
    for name, status in [("   ", 422), ("x" * 61, 422), ("Other", 409)]:
        assert client.patch(f"{BASE}/custom_research", json={"name": name}).status_code == status
    assert client.patch(f"{BASE}/layout1", json={"name": "Renamed preset"}).status_code == 403
    assert client.delete(f"{BASE}/layout1").status_code == 403
    assert client.patch(f"{BASE}/custom_missing", json={"name": "Missing"}).status_code == 404
    assert client.get(BASE).json()["layouts"][1]["name"] == "Research"


def test_delete_active_layout_selects_default_and_preserves_script_library(library):
    client, app, db = library
    sign_in(app)
    save(client)
    save(client, "layout1", name="Layout 1", scripts=[], indicators=[], params={})
    client.post(f"{BASE}/active", json={"layout_id": "custom_research"})
    response = client.delete(f"{BASE}/custom_research")
    assert response.status_code == 200
    assert response.json()["active_layout_id"] == "layout1"
    assert [item["id"] for item in response.json()["layouts"]] == ["layout1"]
    with Session(bind=db.connection(), join_transaction_mode="create_savepoint") as fresh:
        assert fresh.get(ChartLayout, (app.state.user_ids[0], "custom_research")) is None
        script = fresh.get(OpenScript, (app.state.user_ids[0], "user_indicator"))
        assert script is not None and script.visible is False
    assert client.delete(f"{BASE}/custom_research").status_code == 404


def test_delete_inactive_layout_keeps_current_selection_and_scripts(library):
    client, app, db = library
    sign_in(app)
    save(client)
    save(client, "custom_other", name="Other", scripts=[])
    client.post(f"{BASE}/active", json={"layout_id": "custom_research"})
    response = client.delete(f"{BASE}/custom_other")
    assert response.status_code == 200
    assert response.json()["active_layout_id"] == "custom_research"
    db.expire_all()
    assert db.get(OpenScript, (app.state.user_ids[0], "user_indicator")).visible is True


def test_delete_only_layout_clears_selection_and_cannot_modify_another_account(library):
    client, app, db = library
    sign_in(app)
    save(client)
    client.post(f"{BASE}/active", json={"layout_id": "custom_research"})
    sign_in(app, 1)
    assert client.patch(f"{BASE}/custom_research", json={"name": "Stolen"}).status_code == 404
    assert client.delete(f"{BASE}/custom_research").status_code == 404
    sign_in(app)
    assert client.get(BASE).json()["layouts"][0]["name"] == "Research"
    assert client.delete(f"{BASE}/custom_research").json() == {"layouts": [], "active_layout_id": None}
    db.expire_all()
    assert db.get(OpenScript, (app.state.user_ids[0], "user_indicator")).visible is False
