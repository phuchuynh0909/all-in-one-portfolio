"""Tests for the OpenAI-compatible embeddings backend.

All HTTP is mocked (no live embedding server). Covers backend selection, the
request payload sent to ``/v1/embeddings`` (model, input, dimensions, auth
header), and that ``data[]`` is parsed back in ``index`` order.
"""
from __future__ import annotations

from unittest import mock

import pytest

from app.services import embeddings


@pytest.fixture(autouse=True)
def _openai_env(monkeypatch):
    monkeypatch.setenv("RAG_EMBED_BACKEND", "openai")
    monkeypatch.setenv("RAG_OPENAI_EMBED_URL", "http://localhost:20128/v1")
    monkeypatch.setenv("RAG_OPENAI_API_KEY", "sk-test-key")
    monkeypatch.setenv("RAG_OPENAI_EMBED_MODEL", "openrouter/qwen/qwen3-embedding-8b")
    monkeypatch.setenv("RAG_EMBED_DIMENSIONS", "4096")


def test_backend_and_model_selection():
    assert embeddings.backend() == "openai"
    assert embeddings.model_name() == "openrouter/qwen/qwen3-embedding-8b"


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_openai_embed_payload_and_auth():
    captured = {}

    def _post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _Resp({"data": [{"embedding": [0.1, 0.2], "index": 0}]})

    with mock.patch("requests.post", side_effect=_post):
        out = embeddings.openai_embed(["hello"])

    assert captured["url"] == "http://localhost:20128/v1/embeddings"
    assert captured["json"] == {
        "model": "openrouter/qwen/qwen3-embedding-8b",
        "input": ["hello"],
        "dimensions": 4096,
    }
    assert captured["headers"]["Authorization"] == "Bearer sk-test-key"
    assert out == [[0.1, 0.2]]


def test_openai_embed_orders_by_index():
    # Server may return data out of order; we must sort by ``index``.
    payload = {
        "data": [
            {"embedding": [2.0], "index": 1},
            {"embedding": [1.0], "index": 0},
        ]
    }
    with mock.patch("requests.post", return_value=_Resp(payload)):
        out = embeddings.openai_embed(["a", "b"])
    assert out == [[1.0], [2.0]]


def test_openai_embed_raises_on_http_error():
    resp = _Resp({})
    resp.status_code = 500
    resp.text = "boom"  # type: ignore[attr-defined]
    with mock.patch("requests.post", return_value=resp):
        with pytest.raises(RuntimeError):
            embeddings.openai_embed(["x"])


def test_embed_routes_to_openai(monkeypatch):
    with mock.patch(
        "requests.post",
        return_value=_Resp({"data": [{"embedding": [0.5], "index": 0}]}),
    ):
        assert embeddings.embed(["hi"]) == [[0.5]]
