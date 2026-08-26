"""Route wiring for corporate actions. Service behaviour is tested elsewhere.

Every request runs on the rolled-back ``db`` fixture rather than on its own
session against the live host: ``TestClient(app)`` with the real ``get_db`` would
open a genuine connection to ``my_portfolio`` per request. These seven tests
happen to be write-safe, but nothing structural kept them that way, and one
careless POST would land on real financial records.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db.base import get_db
from app.main import app
from tests.conftest import requires_mysql

pytestmark = requires_mysql

BASE = "/api/v1/portfolio/corporate-actions"


@pytest.fixture
def client(db):
    """A client whose requests share the fixture's rolled-back session.

    The override is removed afterwards so it cannot leak into another module's
    ``TestClient``.
    """
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_list_returns_200_and_a_list(client):
    response = client.get(BASE)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_list_accepts_status_and_symbol_filters(client):
    assert client.get(BASE, params={"status": "applied", "symbol": "VCG"}).status_code == 200
    assert client.get(BASE, params={"status": "all"}).status_code == 200


def test_apply_unknown_id_is_404(client):
    assert client.post(f"{BASE}/99999999/apply").status_code == 404


def test_unapply_unknown_id_is_404(client):
    assert client.post(f"{BASE}/99999999/unapply").status_code == 404


def test_ignore_unknown_id_is_404(client):
    assert client.post(f"{BASE}/99999999/ignore").status_code == 404


def test_manual_dividend_validation_rejects_a_missing_amount(client):
    response = client.post("/api/v1/portfolio/dividends", json={
        "symbol": "TST", "action_type": "cash", "ex_date": "2026-01-01",
    })
    assert response.status_code == 400


def test_manual_dividend_rejects_an_unknown_action_type(client):
    response = client.post("/api/v1/portfolio/dividends", json={
        "symbol": "TST", "action_type": "rights", "ex_date": "2026-01-01",
    })
    assert response.status_code == 422


def test_applying_an_already_applied_action_is_409(client, db):
    """A conflict, not a 404: the 404 mapping keys off "not found" alone."""
    from datetime import date
    from decimal import Decimal

    from app.db.models.corporate_action import CorporateAction

    action = CorporateAction(
        symbol="TST", event_id=999400001, name="Thưởng cổ phiếu",
        action_type="stock", ex_date=date(2026, 6, 1), ratio=Decimal("0.1"),
        title="Thưởng cổ phiếu tỷ lệ 100:10", source="dnse_history",
        status="applied",
    )
    db.add(action)
    db.flush()

    response = client.post(f"{BASE}/{action.id}/apply")

    assert response.status_code == 409
    assert "already applied" in response.json()["detail"]
