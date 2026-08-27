"""The app must actually call jobs.shutdown() — the hook is easy to forget."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services.tradingagents import jobs


def test_app_shutdown_drains_running_jobs(monkeypatch):
    called: list[bool] = []
    monkeypatch.setattr(jobs, "shutdown", lambda *a, **k: called.append(True))

    # Entering and leaving the context manager fires startup and shutdown.
    with TestClient(app):
        pass

    assert called, "leaving the app without draining jobs strands running threads"
