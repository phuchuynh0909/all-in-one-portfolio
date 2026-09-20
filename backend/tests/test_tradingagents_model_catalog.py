"""Gateway-backed model choices for the TradingAgents frontend."""
from __future__ import annotations

import pytest

from app.services.tradingagents import model_catalog, runner

pytestmark = pytest.mark.real_auth


def test_parse_model_catalog_filters_and_deduplicates_providers():
    payload = {
        "data": [
            {
                "id": "full/deepseek-chat",
                "full": "deepseek/full/deepseek-chat",
                "routedModel": "deepseek-chat",
                "provider": "deepseek",
            },
            {"id": "deepseek-chat", "provider": "deepseek"},
            {"model": "gpt-5", "provider": {"name": "OpenAI"}},
            {"name": "agent-model", "owned_by": "ag"},
            {"id": "claude", "provider": "anthropic"},
        ]
    }

    assert model_catalog.parse_model_catalog(payload) == {
        "deepseek": ["deepseek-chat"],
        "openai": ["gpt-5"],
        "ag": ["agent-model"],
    }


def test_parse_model_catalog_accepts_provider_grouped_payload():
    payload = {
        "deepseek": ["deepseek-reasoner"],
        "openai": [{"id": "gpt-5.1"}],
        "ag": [{"slug": "ag-fast"}],
        "google": ["gemini-pro"],
    }

    assert model_catalog.parse_model_catalog(payload) == {
        "deepseek": ["deepseek-reasoner"],
        "openai": ["gpt-5.1"],
        "ag": ["ag-fast"],
    }


def test_fetch_model_catalog_uses_configured_gateway(monkeypatch):
    calls: dict[str, object] = {}

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return [{"id": "gpt-5", "provider": "openai"}]

    def fake_get(url, timeout):
        calls.update(url=url, timeout=timeout)
        return Response()

    monkeypatch.setenv(
        "TRADINGAGENTS_MODEL_CATALOG_URL",
        "http://192.168.1.3:20128/api/models",
    )
    monkeypatch.setattr(model_catalog.requests, "get", fake_get)

    assert model_catalog.fetch_model_catalog()["openai"] == ["gpt-5"]
    assert calls == {
        "url": "http://192.168.1.3:20128/api/models",
        "timeout": 5,
    }


def test_ag_model_spec_uses_openai_compatible_gateway():
    assert runner.parse_model_spec("ag:agent-model", "deepseek") == (
        "openai_compatible",
        "agent-model",
    )
