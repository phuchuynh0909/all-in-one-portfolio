"""Text embeddings for the report RAG pipeline and the TradingAgents KB search.

Both sides of the RAG system (``tasks/rag_pipeline.py`` writes,
``app/services/tradingagents/kb_search.py`` reads) embed text with the same
model, so the embedding call lives here once.

One backend: an **OpenAI-compatible** ``/v1/embeddings`` API — the same gateway
``app/services/llm.py`` uses for chat, which serves
``openrouter/qwen/qwen3-embedding-8b`` at 4096 dimensions. (Earlier versions also
supported a local Ollama server and an in-process HuggingFace model; both are
gone, so there is nothing to select and no vector-compatibility caveat left.)

Config (env):
    RAG_OPENAI_EMBED_URL    base of the API (``/embeddings`` is appended).
                            Default: the gateway on :20128, resolved to
                            host.docker.internal from inside a container and
                            localhost on the host — the API runs in Docker while
                            the Prefect worker runs on the host, and both import
                            this module.
    RAG_OPENAI_API_KEY      bearer token. Falls back to
                            OPENAI_COMPATIBLE_API_KEY (the name the gateway's key
                            is stored under), then OPENAI_API_KEY.
    RAG_OPENAI_EMBED_MODEL  model id, default openrouter/qwen/qwen3-embedding-8b
    RAG_EMBED_DIMENSIONS    embedding size requested, default 4096
    RAG_EMBED_BATCH         texts per embed call (default 4)
    RAG_EMBED_RETRIES       attempts on transient failures (default 3)
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "openrouter/qwen/qwen3-embedding-8b"
_DEFAULT_PORT = "20128"

# Transient markers: gateway restarts, upstream rate limits and read timeouts all
# clear on a retry; a 4xx that is not 429 will not.
_TRANSIENT_TOKENS = (
    "eof", "connection", "reset", "timeout", "timed out", "refused",
    "(429)", "(500)", "(502)", "(503)", "(504)",
    "overloaded", "temporarily unavailable",
)


def _in_docker() -> bool:
    return os.path.exists("/.dockerenv") or bool(os.getenv("IN_DOCKER"))


def openai_base() -> str:
    """Base URL of the embeddings API (the ``/embeddings`` path is appended)."""
    explicit = os.getenv("RAG_OPENAI_EMBED_URL")
    if explicit:
        return explicit.rstrip("/")
    host = "host.docker.internal" if _in_docker() else "localhost"
    return f"http://{host}:{_DEFAULT_PORT}/v1"


def api_key() -> Optional[str]:
    """Bearer token for the API, or None when nothing is configured."""
    for name in ("RAG_OPENAI_API_KEY", "OPENAI_COMPATIBLE_API_KEY", "OPENAI_API_KEY"):
        token = (os.getenv(name) or "").strip()
        if token:
            return token
    return None


def model_name() -> str:
    return os.getenv("RAG_OPENAI_EMBED_MODEL", _DEFAULT_MODEL)


def embed_dimensions() -> int:
    return int(os.getenv("RAG_EMBED_DIMENSIONS", "4096"))


def batch_size() -> int:
    return max(1, int(os.getenv("RAG_EMBED_BATCH", "4")))


def describe() -> str:
    """One-line summary of the embedding backend, for logs."""
    return (
        f"openai model={model_name()} url={openai_base()} "
        f"key={'set' if api_key() else 'MISSING'}"
    )


def openai_embed(texts: list[str], timeout: int = 300) -> list[list[float]]:
    """POST /v1/embeddings once. Raises on HTTP / connection errors.

    Returns vectors in the same order as ``texts``: the response ``data[]`` is
    sorted by ``index``, since an OpenAI-compatible server may return it out of
    order. The bearer token is read from the environment, never hardcoded.
    """
    import requests

    url = f"{openai_base()}/embeddings"
    headers = {"Content-Type": "application/json"}
    token = api_key()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    resp = requests.post(
        url,
        json={
            "model": model_name(),
            "input": texts,
            "dimensions": embed_dimensions(),
        },
        headers=headers,
        timeout=timeout,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Embed failed ({resp.status_code}) at {url}: {resp.text[:400]}"
        )
    data = resp.json().get("data")
    if not data:
        raise RuntimeError(f"Embed returned no embeddings for {url}")
    ordered = sorted(data, key=lambda item: item.get("index", 0))
    return [list(item["embedding"]) for item in ordered]


def embed(texts: list[str], timeout: int = 300) -> list[list[float]]:
    """Embed a batch of texts, with retries on transient failures.

    On repeated failure with a multi-text batch, falls back to one text at a time
    so a single huge page doesn't wipe the whole job. Raises on definitive
    failure.
    """
    if not texts:
        return []

    last_err: Optional[BaseException] = None
    attempts = max(1, int(os.getenv("RAG_EMBED_RETRIES", "3")))
    for attempt in range(1, attempts + 1):
        try:
            return openai_embed(texts, timeout)
        except Exception as exc:  # noqa: BLE001 — classified below
            last_err = exc
            msg = str(exc).lower()
            if not any(tok in msg for tok in _TRANSIENT_TOKENS) or attempt >= attempts:
                break
            time.sleep(2 * attempt)

    if len(texts) > 1:
        out: list[list[float]] = []
        for t in texts:
            out.extend(embed([t], timeout=timeout))
        return out

    raise RuntimeError(
        f"Embedding failed ({describe()}, {len(texts)} text(s)): {last_err}"
    ) from last_err


def embed_documents(texts: list[str], timeout: int = 300) -> list[list[float]]:
    """Embed ingest-side texts, chunked into ``RAG_EMBED_BATCH``-sized calls."""
    out: list[list[float]] = []
    size = batch_size()
    for i in range(0, len(texts), size):
        out.extend(embed(texts[i : i + size], timeout=timeout))
    return out


def embed_query(text: str, timeout: int = 60) -> Optional[list[float]]:
    """Embed one search query; ``None`` on any failure (retrieval is best-effort)."""
    try:
        vectors = embed([text], timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Query embedding failed (%s)", exc)
        return None
    return vectors[0] if vectors else None
