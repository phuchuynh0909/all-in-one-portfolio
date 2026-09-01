"""The analyze/stream endpoint's contract.

The endpoint no longer drives the run — it starts a job and subscribes. What is
worth pinning here is the shape that changed: the two failure codes, and that a
disconnect is no longer fatal.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.tradingagents import jobs

URL = "/api/v1/trading-agents/analyze/stream"
BODY = {"symbol": "VCG", "trade_date": "2026-08-27", "analysts": ["market"]}


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def backend_is_up(monkeypatch):
    from app.services.tradingagents import runner

    monkeypatch.setattr(runner, "check_backend", lambda *a, **k: (True, "ok"))


def test_an_unreachable_backend_is_a_503(client, monkeypatch):
    from app.services.tradingagents import runner

    monkeypatch.setattr(runner, "check_backend", lambda *a, **k: (False, "Ollama down"))
    started: list = []
    monkeypatch.setattr(jobs, "start", lambda *a, **k: started.append(a))

    response = client.post(URL, json=BODY)

    assert response.status_code == 503
    assert "Ollama down" in response.json()["detail"]
    # A run that cannot start must not occupy a slot in the registry.
    assert not started


def test_over_the_cap_is_a_429(client, monkeypatch):
    def refuse(*args, **kwargs):
        raise jobs.TooManyRuns("3 analyses already running (limit 3)")

    monkeypatch.setattr(jobs, "start", refuse)

    response = client.post(URL, json=BODY)

    assert response.status_code == 429
    assert "already running" in response.json()["detail"]


def test_the_stream_carries_the_job_events(client, monkeypatch):
    job = jobs.Job(symbol="VCG", trade_date="2026-08-27")
    monkeypatch.setattr(jobs, "start", lambda *a, **k: job)
    monkeypatch.setattr(
        jobs,
        "subscribe",
        lambda j: iter([("started", {"symbol": "VCG"}), ("done", {})]),
    )

    response = client.post(URL, json=BODY)

    assert response.status_code == 200
    body = response.text
    assert "event: started" in body
    assert 'data: {"symbol": "VCG"}' in body
    assert "event: done" in body


def test_the_endpoint_does_not_close_the_job(client, monkeypatch):
    # The old code closed the runner's generator in a finally. If anything still
    # does, this run would be cut short rather than left alone.
    job = jobs.Job(symbol="VCG", trade_date="2026-08-27")
    monkeypatch.setattr(jobs, "start", lambda *a, **k: job)
    monkeypatch.setattr(jobs, "subscribe", lambda j: iter([("done", {})]))

    client.post(URL, json=BODY)

    assert not job.stop_requested
    assert not job.done


def test_the_stream_tells_nginx_not_to_buffer_it(client, monkeypatch):
    """Without this the proxy holds events — heartbeats included — until its
    buffer fills, which defeats both the keepalive and the live progress."""
    from app.services.tradingagents import runner

    monkeypatch.setattr(
        runner, "run_analysis_stream", lambda *a, **k: iter([("done", {})])
    )
    with client.stream("POST", URL, json=BODY) as resp:
        assert resp.status_code == 200
        assert resp.headers["x-accel-buffering"] == "no"
        assert resp.headers["cache-control"] == "no-cache"
