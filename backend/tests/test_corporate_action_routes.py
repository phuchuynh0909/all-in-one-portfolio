"""Route wiring for corporate actions. Service behaviour is tested elsewhere."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import requires_mysql

pytestmark = requires_mysql

client = TestClient(app)
BASE = "/api/v1/portfolio/corporate-actions"


def test_list_returns_200_and_a_list():
    response = client.get(BASE)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_list_accepts_status_and_symbol_filters():
    assert client.get(BASE, params={"status": "applied", "symbol": "VCG"}).status_code == 200
    assert client.get(BASE, params={"status": "all"}).status_code == 200


def test_apply_unknown_id_is_404():
    assert client.post(f"{BASE}/99999999/apply").status_code == 404


def test_unapply_unknown_id_is_404():
    assert client.post(f"{BASE}/99999999/unapply").status_code == 404


def test_ignore_unknown_id_is_404():
    assert client.post(f"{BASE}/99999999/ignore").status_code == 404


def test_manual_dividend_validation_rejects_a_missing_amount():
    response = client.post("/api/v1/portfolio/dividends", json={
        "symbol": "TST", "action_type": "cash", "ex_date": "2026-01-01",
    })
    assert response.status_code == 400


def test_manual_dividend_rejects_an_unknown_action_type():
    response = client.post("/api/v1/portfolio/dividends", json={
        "symbol": "TST", "action_type": "rights", "ex_date": "2026-01-01",
    })
    assert response.status_code == 422
