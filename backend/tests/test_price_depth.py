"""Offline contract tests for accumulated price depth."""
from types import SimpleNamespace
from typing import cast

from clickhouse_connect.driver.client import Client
from fastapi.testclient import TestClient

from app.db.clickhouse import get_clickhouse_client
from app.main import app
from app.services.trade_flow_service import TradeFlowService


class FakeClickHouseClient:
    def __init__(self, rows):
        self.rows = rows
        self.last_sql = ""
        self.last_params = {}

    def query(self, sql, parameters=None):
        self.last_sql = sql
        self.last_params = parameters or {}
        return SimpleNamespace(result_rows=self.rows)


def test_price_depth_accumulates_requested_trading_sessions():
    client = FakeClickHouseClient(
        [
            ["2026-09-08", 5, 25.4, 125_000, 75_000],
            ["2026-09-08", 5, 25.3, 50_000, 80_000],
        ]
    )

    depth = TradeFlowService(cast(Client, client)).get_price_depth("FPT", days=5)

    assert client.last_params == {"symbol": "FPT", "days": 5}
    assert "WITH recent_sessions AS" in client.last_sql
    assert "LIMIT {days:UInt8}" in client.last_sql
    assert "IN (" in client.last_sql
    assert "SELECT session_date FROM recent_sessions" in client.last_sql
    assert depth.model_dump() == {
        "symbol": "FPT",
        "session_date": "2026-09-08",
        "session_count": 5,
        "levels": [
            {"price": 25.4, "buy_size": 125_000, "sell_size": 75_000},
            {"price": 25.3, "buy_size": 50_000, "sell_size": 80_000},
        ],
        "total_buy_size": 175_000,
        "total_sell_size": 155_000,
        "note": None,
    }


def test_depth_route_accepts_integer_days_query():
    fake = FakeClickHouseClient([["2026-09-08", 1, 25.4, 125_000, 75_000]])
    app.dependency_overrides[get_clickhouse_client] = lambda: fake
    try:
        http_client = TestClient(app)
        response = http_client.get(
            "/api/v1/trade-flow/depth",
            params={"symbol": "VCG", "days": 1},
        )
        invalid_response = http_client.get(
            "/api/v1/trade-flow/depth",
            params={"symbol": "VCG", "days": 2},
        )
    finally:
        app.dependency_overrides.pop(get_clickhouse_client, None)

    assert response.status_code == 200
    assert invalid_response.status_code == 422
    assert fake.last_params == {"symbol": "VCG", "days": 1}
