"""Offline tests for the /block-episodes route and its service.

No ClickHouse: the `get_clickhouse_client` dependency is overridden with a fake
client that records the SQL + parameters and returns canned rows. Covers the
response mapping, optional filters (side / candidate_type / min notional), the
date defaults, and validation of a bad candidate_type.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.clickhouse import get_clickhouse_client
from app.services.block_episodes_service import BlockEpisodesService


from clickhouse_connect.driver.exceptions import DatabaseError


class FakeCHClient:
    def __init__(self, rows, raise_exc=None):
        self._rows = rows
        self._raise_exc = raise_exc
        self.last_sql = None
        self.last_params = None

    def query(self, sql, parameters=None):
        self.last_sql = sql
        self.last_params = parameters or {}
        if self._raise_exc is not None:
            raise self._raise_exc
        return SimpleNamespace(result_rows=self._rows)


def _row(
    symbol="FPT",
    start=datetime(2026, 6, 22, 3, 0, 0, tzinfo=timezone.utc),
    end=datetime(2026, 6, 22, 3, 0, 5, tzinfo=timezone.utc),
    side=1,
    ctype="FLOW_CLUSTER_AND_LARGE_PRINT",
):
    return [
        symbol, start, end, side, ctype,
        1_000_000.0,  # signed_notional
        1_000_000.0,  # abs_notional
        20,           # num_trades
        5,            # num_bins
        3,            # large_print_count
        6.3,          # max_abs_z
        1.0,          # max_abs_imbalance
    ]


def _client_with(rows):
    fake = FakeCHClient(rows)
    app.dependency_overrides[get_clickhouse_client] = lambda: fake
    return TestClient(app), fake


def teardown_function():
    app.dependency_overrides.pop(get_clickhouse_client, None)


# ---------------------------------------------------------------------------
# Response mapping
# ---------------------------------------------------------------------------
def test_returns_mapped_episodes():
    client, _ = _client_with([_row()])
    res = client.get("/api/v1/block-episodes", params={"symbol": "fpt"})
    assert res.status_code == 200
    body = res.json()
    assert body["symbol"] == "FPT"
    assert len(body["episodes"]) == 1
    ep = body["episodes"][0]
    assert ep["side"] == 1 and ep["side_label"] == "BUY"
    assert ep["candidate_type"] == "FLOW_CLUSTER_AND_LARGE_PRINT"
    assert ep["start_epoch"] == int(
        datetime(2026, 6, 22, 3, 0, 0, tzinfo=timezone.utc).timestamp()
    )
    assert ep["duration_seconds"] == 5
    assert ep["abs_notional"] == 1_000_000.0
    assert ep["large_print_count"] == 3


def test_symbol_is_uppercased_in_params():
    client, fake = _client_with([])
    client.get("/api/v1/block-episodes", params={"symbol": "hpg"})
    assert fake.last_params["symbol"] == "HPG"


def test_sell_side_label():
    client, _ = _client_with([_row(side=2)])
    res = client.get("/api/v1/block-episodes", params={"symbol": "FPT"})
    ep = res.json()["episodes"][0]
    assert ep["side"] == 2 and ep["side_label"] == "SELL"


# ---------------------------------------------------------------------------
# Filters flow into the SQL params
# ---------------------------------------------------------------------------
def test_side_and_type_and_min_notional_filters_applied():
    client, fake = _client_with([])
    res = client.get(
        "/api/v1/block-episodes",
        params={
            "symbol": "FPT",
            "side": 1,
            "candidate_type": "FLOW_CLUSTER",
            "min_abs_notional": 500000,
            "limit": 50,
        },
    )
    assert res.status_code == 200
    assert fake.last_params["side"] == 1
    assert fake.last_params["candidate_type"] == "FLOW_CLUSTER"
    assert fake.last_params["min_abs_notional"] == 500000.0
    assert fake.last_params["limit"] == 50
    assert "side = " in fake.last_sql
    assert "candidate_type = " in fake.last_sql
    assert "abs_notional >= " in fake.last_sql


def test_no_filters_omits_optional_clauses():
    client, fake = _client_with([])
    client.get("/api/v1/block-episodes", params={"symbol": "FPT"})
    assert "side = " not in fake.last_sql
    assert "candidate_type = " not in fake.last_sql
    assert "abs_notional >= " not in fake.last_sql
    # 30-day default lookback: from < to.
    assert fake.last_params["from"] < fake.last_params["to"]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def test_bad_candidate_type_returns_400():
    client, _ = _client_with([])
    res = client.get(
        "/api/v1/block-episodes",
        params={"symbol": "FPT", "candidate_type": "NONSENSE"},
    )
    assert res.status_code == 400


def test_bad_side_returns_422():
    client, _ = _client_with([])
    res = client.get("/api/v1/block-episodes", params={"symbol": "FPT", "side": 9})
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# Service unit (direct, no HTTP)
# ---------------------------------------------------------------------------
def test_missing_table_returns_empty_not_500():
    # Fresh cluster: table not created yet -> empty result, not an error.
    err = DatabaseError(
        "Code: 60. DB::Exception: Unknown table expression identifier "
        "'block_episodes' ... (UNKNOWN_TABLE)"
    )
    fake = FakeCHClient([], raise_exc=err)
    app.dependency_overrides[get_clickhouse_client] = lambda: fake
    client = TestClient(app)
    res = client.get("/api/v1/block-episodes", params={"symbol": "FPT"})
    assert res.status_code == 200
    assert res.json() == {"symbol": "FPT", "episodes": []}


def test_other_database_error_propagates():
    err = DatabaseError("Code: 999. DB::Exception: something else")
    fake = FakeCHClient([], raise_exc=err)
    svc = BlockEpisodesService(fake)
    with pytest.raises(DatabaseError):
        svc.get_episodes("FPT", date(2026, 6, 1), date(2026, 6, 22))


def test_service_explicit_date_range():
    fake = FakeCHClient([_row()])
    svc = BlockEpisodesService(fake)
    resp = svc.get_episodes("FPT", date(2026, 6, 1), date(2026, 6, 22))
    assert resp.symbol == "FPT"
    assert fake.last_params["from"] == "2026-06-01"
    assert fake.last_params["to"] == "2026-06-22"
    assert len(resp.episodes) == 1
