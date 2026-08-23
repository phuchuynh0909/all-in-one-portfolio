"""Chat completions against the OpenAI-compatible gateway.

Companion to :mod:`app.services.embeddings`: that module owns the *embed* call,
this one owns the *chat* call, so both sides of the RAG pipeline have exactly one
place that knows how to reach a model. Used by ``tasks/rag_pipeline.py`` to
condense a parsed report into a sectioned digest before chunking.

The gateway is the same OpenAI-compatible service TradingAgents talks to
(``TRADINGAGENTS_LLM_BACKEND_URL``, default port 20128), so the key falls back to
``OPENAI_COMPATIBLE_API_KEY`` — the provider-suffixed name TradingAgents already
uses for it. Note that generic ``OPENAI_API_KEY`` is *not* accepted by that
gateway; it is tried last only so a plain OpenAI endpoint still works.

Config (env):
    RAG_LLM_URL          base of the OpenAI-compatible API ("/chat/completions"
                         is appended). Default: the gateway on :20128, resolved
                         to host.docker.internal from inside a container and
                         localhost on the host (same split as embeddings.py).
    RAG_LLM_API_KEY      bearer token. Falls back to OPENAI_COMPATIBLE_API_KEY,
                         then RAG_OPENAI_API_KEY, then OPENAI_API_KEY.
    RAG_LLM_MODEL        model id, default ``cx/gpt-5.6-luna``. A TradingAgents
                         -style reasoning suffix (``model(high)``) is stripped.
    RAG_LLM_MAX_TOKENS   max completion tokens per call, default 16000
    RAG_LLM_TEMPERATURE  optional; omitted entirely when unset, because some
                         reasoning models reject any explicit temperature
    RAG_LLM_TIMEOUT      per-request seconds, default 600
    RAG_LLM_RETRIES      attempts on transient failures, default 3
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "cx/gpt-5.6-luna"
_DEFAULT_PORT = "20128"

# A trailing reasoning-effort annotation, e.g. "cx/gpt-5.6-sol(high)" — the
# TradingAgents env vars carry it, the raw HTTP API does not accept it.
_EFFORT_SUFFIX = re.compile(r"\((?:minimal|low|medium|high|max)\)\s*$", re.I)

# Transient markers: gateway restarts, upstream rate limits and read timeouts all
# clear on a retry; a 4xx that is not 429 will not.
_TRANSIENT_TOKENS = (
    "eof", "connection", "reset", "timeout", "timed out", "refused",
    "(429)", "(500)", "(502)", "(503)", "(504)",
    "overloaded", "temporarily unavailable", "empty completion",
)


def _in_docker() -> bool:
    return os.path.exists("/.dockerenv") or bool(os.getenv("IN_DOCKER"))


def base_url() -> str:
    """Base URL of the gateway; ``/chat/completions`` is appended to it.

    ``RAG_LLM_URL`` wins. Otherwise the TradingAgents gateway URL is reused, but
    with its host re-resolved for this process: that env var is written for the
    containerized API (``host.docker.internal``) while the Prefect worker runs on
    the host, where only ``localhost`` resolves.
    """
    explicit = os.getenv("RAG_LLM_URL")
    if explicit:
        return explicit.rstrip("/")

    host = "host.docker.internal" if _in_docker() else "localhost"
    shared = os.getenv("TRADINGAGENTS_LLM_BACKEND_URL")
    if shared:
        # Keep the gateway's port/path, swap the host for one this process can reach.
        return re.sub(r"//[^/:]+", f"//{host}", shared.rstrip("/"), count=1)
    return f"http://{host}:{_DEFAULT_PORT}/v1"


def api_key() -> Optional[str]:
    """Bearer token for the gateway, or None when nothing is configured."""
    for name in (
        "RAG_LLM_API_KEY",
        "OPENAI_COMPATIBLE_API_KEY",
        "RAG_OPENAI_API_KEY",
        "OPENAI_API_KEY",
    ):
        token = (os.getenv(name) or "").strip()
        if token:
            return token
    return None


def model_name() -> str:
    """Configured model id, without any reasoning-effort suffix."""
    raw = (os.getenv("RAG_LLM_MODEL") or _DEFAULT_MODEL).strip()
    return _EFFORT_SUFFIX.sub("", raw).strip()


def max_tokens() -> int:
    return int(os.getenv("RAG_LLM_MAX_TOKENS", "16000"))


def describe() -> str:
    """One-line summary of the active chat backend, for logs."""
    return (
        f"openai-compatible model={model_name()} url={base_url()} "
        f"key={'set' if api_key() else 'MISSING'}"
    )


def available() -> bool:
    """Whether a key is configured. The URL always has a default, the key does not."""
    return bool(api_key())


def _post_once(messages: list[dict[str, str]], *, model: str, limit: int, timeout: int) -> str:
    """One POST /chat/completions. Raises on HTTP error or an empty completion."""
    import requests

    url = f"{base_url()}/chat/completions"
    headers = {"Content-Type": "application/json"}
    token = api_key()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": limit,
    }
    # Only send temperature when explicitly configured — several reasoning models
    # reject any value, including the nominal default.
    temperature = os.getenv("RAG_LLM_TEMPERATURE")
    if temperature not in (None, ""):
        payload["temperature"] = float(temperature)

    resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"chat failed ({resp.status_code}) at {url}: {resp.text[:400]}"
        )

    choices = (resp.json() or {}).get("choices") or []
    content = (choices[0].get("message", {}).get("content") if choices else "") or ""
    content = content.strip()
    if not content:
        # A reasoning model can burn the whole budget on hidden tokens and return
        # an empty message. Classified transient so the retry loop gets a turn.
        raise RuntimeError(
            f"empty completion from {model} (finish_reason="
            f"{choices[0].get('finish_reason') if choices else 'n/a'}); "
            f"consider raising RAG_LLM_MAX_TOKENS"
        )
    return content


def chat(
    messages: list[dict[str, str]],
    *,
    model: Optional[str] = None,
    limit: Optional[int] = None,
    timeout: Optional[int] = None,
) -> str:
    """Run one chat completion and return the assistant text.

    Retries transient gateway failures (restarts, 429/5xx, read timeouts, empty
    completions). Raises ``RuntimeError`` on definitive failure — callers that
    treat generation as best-effort should catch it.
    """
    if not messages:
        raise ValueError("chat() requires at least one message")

    use_model = model or model_name()
    use_limit = limit or max_tokens()
    use_timeout = timeout or int(os.getenv("RAG_LLM_TIMEOUT", "600"))
    attempts = max(1, int(os.getenv("RAG_LLM_RETRIES", "3")))

    last_err: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            return _post_once(
                messages, model=use_model, limit=use_limit, timeout=use_timeout
            )
        except Exception as exc:  # noqa: BLE001 — classified below
            last_err = exc
            msg = str(exc).lower()
            if not any(tok in msg for tok in _TRANSIENT_TOKENS) or attempt >= attempts:
                break
            logger.warning(
                "chat attempt %d/%d failed (%s); retrying", attempt, attempts, exc
            )
            time.sleep(3 * attempt)

    raise RuntimeError(f"chat failed ({describe()}): {last_err}") from last_err
