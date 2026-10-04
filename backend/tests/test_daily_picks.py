from __future__ import annotations

from datetime import date, datetime, timezone
from typing import cast

import pandas as pd
from clickhouse_connect.driver.client import Client
from fastapi.testclient import TestClient

from app.api.v1.routes.daily_picks import get_daily_picks_service
from app.main import app
from app.schemas.daily_picks import DailyPickHistory, DailyPickSnapshot
from app.services.daily_picks_service import DailyPicksService, rank_daily_candidates


def test_ranker_filters_weak_candidates_and_returns_stable_top_n():
    candidates = pd.DataFrame([
        {
            "symbol": "HPG",
            "y_pred_ensemble": 0.75,
            "msr_rank_20": 0.70,
            "trend_20": 0.10,
            "dollar_vol_pctile": 0.95,
            "atr_pct": 0.05,
            "signal_versions": ["v2"],
        },
        {
            "symbol": "FPT",
            "y_pred_ensemble": 0.82,
            "msr_rank_20": 0.90,
            "trend_20": 0.20,
            "dollar_vol_pctile": 0.85,
            "atr_pct": 0.02,
            "signal_versions": ["v1", "v4"],
        },
        {
            "symbol": "VCB",
            "y_pred_ensemble": 0.56,
            "msr_rank_20": 0.99,
            "trend_20": 0.30,
            "dollar_vol_pctile": 0.99,
            "atr_pct": 0.01,
            "signal_versions": ["v1"],
        },
    ])

    picks = rank_daily_candidates(candidates, limit=2, min_probability=0.57)

    assert [pick.symbol for pick in picks] == ["FPT", "HPG"]
    assert [pick.rank for pick in picks] == [1, 2]
    assert picks[0].signal_versions == ["v1", "v4"]
    assert all(pick.model_probability >= 0.57 for pick in picks)
    assert picks[0].score > picks[1].score


def test_ranker_uses_neutral_values_when_optional_features_are_absent():
    picks = rank_daily_candidates(
        pd.DataFrame([{"symbol": "fpt", "y_pred_ensemble": 0.8}]),
        min_probability=0.57,
    )

    assert len(picks) == 1
    assert picks[0].symbol == "FPT"
    assert picks[0].components.relative_strength_rank == 0.5
    assert picks[0].components.liquidity_rank == 0.5
    assert picks[0].risk_flags == []


class _Result:
    def __init__(self, rows):
        self.result_rows = rows


class _HistoryClient:
    def __init__(self):
        generated_old = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
        generated_new = datetime(2026, 10, 2, 9, tzinfo=timezone.utc)
        components = (
            '{"model_probability":0.8,"relative_strength_rank":0.9,'
            '"trend_rank":0.7,"liquidity_rank":0.8,'
            '"volatility_safety_rank":0.6}'
        )
        self.results = [
            _Result([
                (date(2026, 10, 2), generated_new, "m2", 198, 105, 4),
                (date(2026, 10, 1), generated_old, "m1", 198, 103, 3),
            ]),
            _Result([
                (date(2026, 10, 1), generated_old, 1, "FPT", 82.0, 0.8, ["v1"], components, []),
                (date(2026, 10, 2), generated_new, 1, "HPG", 78.0, 0.76, ["v2"], components, []),
            ]),
        ]

    def command(self, _sql):
        return None

    def query(self, sql):
        if "max(generated_at) AS generated_at" in sql:
            raise AssertionError(
                "ClickHouse code 184: aggregate max(generated_at) was resolved "
                "inside argMax(..., generated_at)"
            )
        return self.results.pop(0)


def test_history_reader_batches_picks_and_orders_sessions_oldest_first(tmp_path):
    watchlist = tmp_path / "watchlist.csv"
    watchlist.write_text("FPT\\nHPG\\n", encoding="utf-8")
    history = DailyPicksService(cast(Client, _HistoryClient()), watchlist).get_history(limit=2)

    assert [snapshot.as_of.isoformat() for snapshot in history.snapshots] == [
        "2026-10-01",
        "2026-10-02",
    ]
    assert [snapshot.picks[0].symbol for snapshot in history.snapshots] == ["FPT", "HPG"]


class _SnapshotReader:
    def __init__(self, snapshot: DailyPickSnapshot | None):
        self.snapshot = snapshot

    def get_snapshot(self, as_of: date | None = None):
        if self.snapshot is None:
            return None
        if as_of is not None and as_of != self.snapshot.as_of:
            return None
        return self.snapshot

    def get_history(self, limit: int = 60):
        snapshots = [] if self.snapshot is None else [self.snapshot]
        return DailyPickHistory(snapshots=snapshots[-limit:])


def _snapshot() -> DailyPickSnapshot:
    return DailyPickSnapshot(
        as_of=date(2026, 10, 2),
        generated_at=datetime(2026, 10, 2, 8, 30, tzinfo=timezone.utc),
        model_version="20261002",
        universe_size=198,
        eligible_size=106,
        candidate_size=0,
        picks=[],
    )


def test_latest_route_returns_the_persisted_snapshot():
    app.dependency_overrides[get_daily_picks_service] = lambda: _SnapshotReader(_snapshot())
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/daily-picks/latest")
    finally:
        app.dependency_overrides.pop(get_daily_picks_service, None)

    assert response.status_code == 200
    assert response.json()["as_of"] == "2026-10-02"
    assert response.json()["model_version"] == "20261002"


def test_history_route_returns_snapshots_for_dashboard():
    app.dependency_overrides[get_daily_picks_service] = lambda: _SnapshotReader(_snapshot())
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/daily-picks/history", params={"limit": 20})
    finally:
        app.dependency_overrides.pop(get_daily_picks_service, None)

    assert response.status_code == 200
    assert [snapshot["as_of"] for snapshot in response.json()["snapshots"]] == [
        "2026-10-02"
    ]


def test_date_route_returns_404_when_snapshot_does_not_exist():
    app.dependency_overrides[get_daily_picks_service] = lambda: _SnapshotReader(None)
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/daily-picks/2026-10-02")
    finally:
        app.dependency_overrides.pop(get_daily_picks_service, None)

    assert response.status_code == 404
    assert "2026-10-02" in response.json()["detail"]
