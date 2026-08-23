"""Text embeddings for the report RAG pipeline and the TradingAgents KB search.

Both sides of the RAG system (``tasks/rag_pipeline.py`` writes,
``app/services/tradingagents/kb_search.py`` reads) embed text with the same
model, so the embedding call lives here once. Two interchangeable backends,
selected with ``RAG_EMBED_BACKEND``:

    ollama       (default) HTTP POST to an Ollama server's ``/api/embed``.
    huggingface  the model runs **in this process** via sentence-transformers
                 (no server, no API); default ``Qwen/Qwen3-Embedding-8B``.
    openai       HTTP POST to an OpenAI-compatible ``/v1/embeddings`` service
                 (vLLM / LiteLLM / OpenRouter proxy); default model
                 ``openrouter/qwen/qwen3-embedding-8b`` at 4096 dimensions.

Both default to Qwen3-Embedding-8B, so vectors stay dimension-compatible
(4096) and a collection embedded through one backend remains searchable
through the other. Re-embedding after a switch is still the safer choice if
retrieval quality looks off — pooling/normalization details differ slightly
between the two runtimes.

Config (env):
    RAG_EMBED_BACKEND    "ollama" (default) | "huggingface" ("hf"/"local")
                         | "openai" ("openai-compat"/"vllm"/"litellm")
    RAG_EMBED_BATCH      texts per embed call (default 4)
    RAG_EMBED_RETRIES    retries on transient failures (default 3)
    RAG_EMBED_MODEL      ollama model tag, default qwen3-embedding:8b
    RAG_OLLAMA_URL / OLLAMA_BASE_URL   ollama server root; defaults to
                         host.docker.internal:11434 in Docker, else localhost
    RAG_HF_EMBED_MODEL   HF repo id or local path, default Qwen/Qwen3-Embedding-8B
    RAG_HF_DTYPE         weight dtype for the local model, default "auto"
                         (the model config's own — bfloat16 for Qwen3-Embedding;
                         "float32" would need ~32 GB for the 8B)
    RAG_OPENAI_EMBED_URL base of the OpenAI-compatible API (``/embeddings`` is
                         appended), default http://localhost:20128/v1
    RAG_OPENAI_API_KEY   bearer token (falls back to OPENAI_API_KEY)
    RAG_OPENAI_EMBED_MODEL  model id, default openrouter/qwen/qwen3-embedding-8b
    RAG_EMBED_DIMENSIONS embedding size requested from the openai backend,
                         default 4096 (keeps vectors compatible with the others)
"""
from __future__ import annotations

import logging
import os
import time
from functools import lru_cache
from typing import Any, Optional

logger = logging.getLogger(__name__)

_OLLAMA = "ollama"
_HUGGINGFACE = "huggingface"
_OPENAI = "openai"


def backend() -> str:
    """Which embedding backend is configured (normalized name)."""
    raw = (os.getenv("RAG_EMBED_BACKEND") or _OLLAMA).strip().lower()
    if raw in ("hf", "huggingface", "sentence-transformers", "local"):
        return _HUGGINGFACE
    if raw in ("openai", "openai-compat", "openai-api", "vllm", "litellm"):
        return _OPENAI
    if raw in ("ollama", "api", "http"):
        return _OLLAMA
    raise ValueError(
        f"Unknown RAG_EMBED_BACKEND={raw!r}; use 'ollama', 'huggingface' or 'openai'."
    )


def model_name() -> str:
    """Model id of the active backend."""
    if backend() == _HUGGINGFACE:
        return os.getenv("RAG_HF_EMBED_MODEL", "Qwen/Qwen3-Embedding-8B")
    if backend() == _OPENAI:
        return os.getenv("RAG_OPENAI_EMBED_MODEL", "openrouter/qwen/qwen3-embedding-8b")
    return os.getenv("RAG_EMBED_MODEL", "qwen3-embedding:8b")


def batch_size() -> int:
    return max(1, int(os.getenv("RAG_EMBED_BATCH", "4")))


def describe() -> str:
    """One-line summary of the active backend, for logs."""
    if backend() == _HUGGINGFACE:
        return f"huggingface model={model_name()} (in-process)"
    if backend() == _OPENAI:
        return f"openai model={model_name()} url={openai_base()}"
    return f"ollama model={model_name()} host={ollama_base()}"


# ---------------------------------------------------------------------------
# ollama backend
# ---------------------------------------------------------------------------


def _default_ollama_host() -> str:
    """``host.docker.internal`` from inside a container, ``localhost`` on the host.

    The API runs in Docker while the Prefect worker runs on the host, and both
    import this module — so the fallback has to differ per process.
    """
    in_docker = os.path.exists("/.dockerenv") or bool(os.getenv("IN_DOCKER"))
    return "http://host.docker.internal:11434" if in_docker else "http://localhost:11434"


def ollama_base() -> str:
    """Root URL of the Ollama server (native ``/api``, not the OpenAI ``/v1`` path)."""
    base = (
        os.getenv("RAG_OLLAMA_URL")
        or os.getenv("OLLAMA_BASE_URL")
        or _default_ollama_host()
    ).rstrip("/")
    # TradingAgents often sets OLLAMA_BASE_URL with a trailing /v1 — strip it so
    # we hit Ollama's native /api/embed, not the OpenAI-compat shim.
    if base.endswith("/v1"):
        base = base[:-3]
    return base


def ollama_embed(texts: list[str], timeout: int = 300) -> list[list[float]]:
    """POST /api/embed once. Raises on HTTP / connection errors."""
    import requests

    url = f"{ollama_base()}/api/embed"
    resp = requests.post(
        url, json={"model": model_name(), "input": texts}, timeout=timeout
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Ollama embed failed ({resp.status_code}) at {url}: {resp.text[:400]}"
        )
    embeddings = resp.json().get("embeddings")
    if not embeddings:
        raise RuntimeError(f"Ollama embed returned no embeddings for {url}")
    return [list(vec) for vec in embeddings]


# ---------------------------------------------------------------------------
# openai-compatible backend (vLLM / LiteLLM / OpenRouter proxy)
# ---------------------------------------------------------------------------


def openai_base() -> str:
    """Base URL of the OpenAI-compatible API (the ``/embeddings`` path is added)."""
    return os.getenv("RAG_OPENAI_EMBED_URL", "http://localhost:20128/v1").rstrip("/")


def embed_dimensions() -> int:
    return int(os.getenv("RAG_EMBED_DIMENSIONS", "4096"))


def openai_embed(texts: list[str], timeout: int = 300) -> list[list[float]]:
    """POST /v1/embeddings once. Raises on HTTP / connection errors.

    Returns vectors in the same order as ``texts`` (the response ``data[]`` is
    sorted by ``index``, since an OpenAI-compatible server may return it out of
    order). The bearer token is read from the environment, never hardcoded.
    """
    import requests

    url = f"{openai_base()}/embeddings"
    headers = {"Content-Type": "application/json"}
    token = os.getenv("RAG_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
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
            f"OpenAI embed failed ({resp.status_code}) at {url}: {resp.text[:400]}"
        )
    data = resp.json().get("data")
    if not data:
        raise RuntimeError(f"OpenAI embed returned no embeddings for {url}")
    ordered = sorted(data, key=lambda item: item.get("index", 0))
    return [list(item["embedding"]) for item in ordered]


# ---------------------------------------------------------------------------
# huggingface backend (in-process sentence-transformers)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _hf_model() -> Any:
    """Lazy-load the sentence-transformers model once per process.

    sentence-transformers picks the best available device itself (cuda / mps /
    cpu). ``dtype="auto"`` honours the model config — bfloat16 for
    Qwen3-Embedding, i.e. ~16 GB resident for the 8B rather than fp32's ~32 GB.
    """
    from sentence_transformers import SentenceTransformer

    name = model_name()
    logger.info("Loading HF embedder %s …", name)
    model = SentenceTransformer(
        name, model_kwargs={"dtype": os.getenv("RAG_HF_DTYPE", "auto")}
    )
    # Qwen3-Embedding pools the *last* token, so padding has to go on the left
    # or short texts in a batch get pooled off their pad tokens.
    if getattr(model, "tokenizer", None) is not None:
        model.tokenizer.padding_side = "left"
    return model


def hf_embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of strings locally (normalized)."""
    return (
        _hf_model()
        .encode(texts, normalize_embeddings=True, show_progress_bar=False)
        .tolist()
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Transient markers: a crashed Ollama model runner (OOM / contention) reports
# back as "llama-server process no longer running" or a 500/503, and Ollama
# restarts the runner on the next call. Local torch OOM often clears too once
# the failed batch is retried one text at a time.
_TRANSIENT_TOKENS = (
    "eof", "connection", "reset", "timeout", "refused",
    "no longer running", "llama runner", "runner process",
    "(500)", "(502)", "(503)",
    "out of memory", "cuda error",
)


def embed(texts: list[str], timeout: int = 300) -> list[list[float]]:
    """Embed a batch of texts, with retries on transient failures.

    On repeated failure with a multi-text batch, falls back to one text at a
    time so a single huge page doesn't wipe the whole job. ``timeout`` applies
    to the ollama backend only. Raises on definitive failure.
    """
    if not texts:
        return []

    active = backend()
    last_err: Optional[BaseException] = None
    attempts = max(1, int(os.getenv("RAG_EMBED_RETRIES", "3")))
    for attempt in range(1, attempts + 1):
        try:
            if active == _HUGGINGFACE:
                return hf_embed(texts)
            if active == _OPENAI:
                return openai_embed(texts, timeout)
            return ollama_embed(texts, timeout)
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
