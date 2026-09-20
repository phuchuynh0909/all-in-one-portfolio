"""Model catalog fetched from the platform's LLM gateway."""
from __future__ import annotations

import os
from typing import Any, Iterable

import requests

DEFAULT_MODEL_CATALOG_URL = "http://192.168.1.3:20128/api/models"
ALLOWED_MODEL_PROVIDERS = ("deepseek", "openai", "ag")


def _provider_name(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("id") or value.get("name") or value.get("slug")
    return str(value or "").strip().lower()


def _records(payload: Any) -> Iterable[tuple[str | None, Any]]:
    """Yield ``(group provider, record)`` from common catalog response shapes."""
    if isinstance(payload, list):
        yield from ((None, item) for item in payload)
        return
    if not isinstance(payload, dict):
        return

    for key in ("models", "data", "items", "results"):
        rows = payload.get(key)
        if isinstance(rows, list):
            yield from ((None, item) for item in rows)
            return

    # Also accept a provider-grouped response such as
    # {"deepseek": [{"id": "deepseek-chat"}], "openai": ["gpt-5"]}.
    for provider, rows in payload.items():
        if _provider_name(provider) not in ALLOWED_MODEL_PROVIDERS:
            continue
        if isinstance(rows, list):
            yield from ((provider, item) for item in rows)


def parse_model_catalog(payload: Any) -> dict[str, list[str]]:
    """Normalize and filter the gateway response by supported provider."""
    catalog = {provider: [] for provider in ALLOWED_MODEL_PROVIDERS}
    for grouped_provider, item in _records(payload):
        if isinstance(item, str):
            provider = _provider_name(grouped_provider)
            model = item.strip()
        elif isinstance(item, dict):
            provider = _provider_name(
                item.get("provider")
                or item.get("provider_id")
                or item.get("owned_by")
                or item.get("vendor")
                or grouped_provider
            )
            model = str(
                item.get("routedModel")
                or item.get("routed_model")
                or item.get("id")
                or item.get("model")
                or item.get("name")
                or item.get("slug")
                or ""
            ).strip()
        else:
            continue

        if provider not in catalog or not model or model in catalog[provider]:
            continue
        catalog[provider].append(model)
    print(f"Fetched model catalog: {catalog}")
    return catalog


def fetch_model_catalog() -> dict[str, list[str]]:
    """Fetch the gateway catalog; failures are handled by the API route."""
    url = os.getenv("TRADINGAGENTS_MODEL_CATALOG_URL", DEFAULT_MODEL_CATALOG_URL)
    response = requests.get(url, timeout=5)
    print(f"Fetched model catalog from {url}: {response.status_code}")
    response.raise_for_status()
    return parse_model_catalog(response.json())
