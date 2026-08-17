"""Readiness-probe tests for the tradingagents runner.

The probe must speak each provider's own dialect: true Ollama exposes
``/api/tags`` at the server root, while an OpenAI-compatible gateway (9router /
LiteLLM, provider ``openai_compatible``) exposes ``/v1/models`` behind a bearer
key and answers ``/api/tags`` with 401/404. Probing the wrong path reports a
working gateway as "Ollama not reachable".
"""

from __future__ import annotations

import requests

from app.services.tradingagents import runner


class _Resp:
    def __init__(self, status: int = 200) -> None:
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


def test_openai_compatible_probes_v1_models_with_bearer(monkeypatch):
    calls: dict[str, object] = {}

    def fake_get(url, headers=None, timeout=None):
        calls["url"] = url
        calls["headers"] = headers or {}
        return _Resp(200)

    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-test-123")
    monkeypatch.setattr(requests, "get", fake_get)

    ok, msg = runner._check_provider(
        "openai_compatible", "http://host.docker.internal:20128/v1"
    )

    assert ok, msg
    assert calls["url"] == "http://host.docker.internal:20128/v1/models"
    assert calls["headers"].get("Authorization") == "Bearer sk-test-123"


def test_openai_compatible_appends_v1_when_missing(monkeypatch):
    calls: dict[str, object] = {}

    def fake_get(url, headers=None, timeout=None):
        calls["url"] = url
        return _Resp(200)

    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
    monkeypatch.setattr(requests, "get", fake_get)

    ok, _ = runner._check_provider("openai_compatible", "http://gateway:20128")

    assert ok
    assert calls["url"] == "http://gateway:20128/v1/models"


def test_openai_compatible_401_is_not_reported_as_ollama(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return _Resp(401)

    monkeypatch.setattr(requests, "get", fake_get)

    ok, msg = runner._check_provider(
        "openai_compatible", "http://host.docker.internal:20128/v1"
    )

    assert not ok
    assert "openai_compatible" in msg
    assert "ollama serve" not in msg.lower()


def test_ollama_still_probes_api_tags(monkeypatch):
    calls: dict[str, object] = {}

    def fake_get(url, headers=None, timeout=None):
        calls["url"] = url
        return _Resp(200)

    monkeypatch.setattr(requests, "get", fake_get)

    ok, _ = runner._check_provider("ollama", "http://host.docker.internal:11434/v1")

    assert ok
    assert calls["url"] == "http://host.docker.internal:11434/api/tags"
