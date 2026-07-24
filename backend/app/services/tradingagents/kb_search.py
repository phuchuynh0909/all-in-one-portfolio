"""Knowledge-base retrieval for the TradingAgents news/sentiment + sector analysts.

The report RAG pipeline (``tasks/rag_pipeline.py``) embeds wichart research-report
pages into a Qdrant collection (``wichart_reports``) using an Ollama embedding
model (Qwen3-Embedding-8B). This module is the *read* side: it embeds a query the
same way and runs a semantic search over that collection so the analysts can pull
company/sector research from our own knowledge base **before** falling back to a
live web search.

Everything degrades gracefully — if the KB is disabled, unreachable, empty, or
returns nothing above the score threshold, callers get an empty result and fall
back to web search rather than crashing the run.

Config (env, shared with the RAG pipeline so both sides agree):
    QDRANT_URL                  default http://192.168.1.3:6333
    QDRANT_REPORTS_COLLECTION   default wichart_reports
    RAG_EMBED_MODEL             Ollama embed model, default qwen3-embedding:8b
    RAG_OLLAMA_URL / OLLAMA_BASE_URL   Ollama root, default host.docker.internal:11434
    TRADINGAGENTS_KB_SEARCH     "1" (default) / "0" to disable KB retrieval
    TRADINGAGENTS_KB_MIN_SCORE  cosine-score floor for a "match" (default 0.35)
    TRADINGAGENTS_KB_TOP_K      max chunks returned per query (default 6)
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# marker (the PDF parser) leaves page-separator runs (``{0}------…``) and image
# placeholders (``![](_page_0_Picture_0.jpeg)``) in the chunk text; strip them so
# the analyst prompt sees clean prose.
_MARKER_PAGE_SEP = re.compile(r"\{\d+\}\s*-{5,}")
_MARKER_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def _clean_text(text: str) -> str:
    text = _MARKER_IMAGE.sub("", text)
    text = _MARKER_PAGE_SEP.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

_QDRANT_URL = os.getenv("QDRANT_URL", "http://192.168.1.3:6333")
_COLLECTION = os.getenv("QDRANT_REPORTS_COLLECTION", "wichart_reports")
_EMBED_MODEL = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding:8b")

DEFAULT_TOP_K = int(os.getenv("TRADINGAGENTS_KB_TOP_K", "6"))
DEFAULT_MIN_SCORE = float(os.getenv("TRADINGAGENTS_KB_MIN_SCORE", "0.6"))


def kb_enabled() -> bool:
    """Whether KB retrieval is switched on (default yes)."""
    return os.getenv("TRADINGAGENTS_KB_SEARCH", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _ollama_base() -> str:
    """Root URL of the Ollama server (native ``/api``, not the OpenAI ``/v1`` path)."""
    base = (
        os.getenv("RAG_OLLAMA_URL")
        or os.getenv("OLLAMA_BASE_URL")
        or "http://host.docker.internal:11434"
    ).rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base


def _embed_query(text: str) -> Optional[list[float]]:
    """Embed one query string via Ollama ``/api/embed``; None on any failure."""
    import requests

    try:
        resp = requests.post(
            f"{_ollama_base()}/api/embed",
            json={"model": _EMBED_MODEL, "input": [text]},
            timeout=60,
        )
        resp.raise_for_status()
        embeddings = resp.json().get("embeddings") or []
        if not embeddings:
            return None
        return list(embeddings[0])
    except Exception as exc:  # noqa: BLE001 — retrieval is best-effort
        logger.warning("KB embed failed (%s); skipping knowledge base", exc)
        return None


def _client():
    from qdrant_client import QdrantClient

    # Client/server minor-version drift only warns; skip the check to keep logs clean.
    return QdrantClient(url=_QDRANT_URL, timeout=30, check_compatibility=False)


def _symbol_filter(symbols: Optional[list[str]]):
    """Build a Qdrant filter matching any of ``symbols`` on the ``symbol`` payload.

    Returns None when no symbols are given (unfiltered semantic search).
    """
    syms = [s.strip().upper() for s in (symbols or []) if s and s.strip()]
    if not syms:
        return None
    from qdrant_client import models

    return models.Filter(
        must=[models.FieldCondition(key="symbol", match=models.MatchAny(any=syms))]
    )


def search(
    query: str,
    symbols: Optional[list[str]] = None,
    top_k: int = DEFAULT_TOP_K,
    min_score: float = DEFAULT_MIN_SCORE,
) -> list[dict[str, Any]]:
    """Semantic search over the knowledge base.

    ``symbols`` (optional) restricts hits to those report chunks tagged with one of
    the given tickers. Returns a list of ``{score, symbol, title, page, pdf_url,
    text}`` dicts (highest score first), filtered to ``score >= min_score``. Any
    failure (KB disabled, embed error, Qdrant unreachable) yields ``[]`` so callers
    fall back to web search.
    """
    if not kb_enabled() or not query.strip():
        return []

    vector = _embed_query(query)
    if vector is None:
        return []

    try:
        client = _client()
        try:
            response = client.query_points(
                collection_name=_COLLECTION,
                query=vector,
                query_filter=_symbol_filter(symbols),
                limit=max(1, top_k),
                with_payload=True,
                score_threshold=min_score,
            )
            hits = response.points
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001 — treat as "no match" → web fallback
        logger.warning("KB search failed (%s); falling back to web", exc)
        return []

    out: list[dict[str, Any]] = []
    for h in hits:
        payload = h.payload or {}
        out.append(
            {
                "score": float(h.score),
                "symbol": str(payload.get("symbol") or "").strip(),
                "title": str(payload.get("title") or "").strip(),
                "page": payload.get("page"),
                "pdf_url": str(payload.get("pdf_url") or "").strip(),
                "text": _clean_text(str(payload.get("text") or "")),
            }
        )
    return out


def format_hits(header: str, hits: list[dict[str, Any]], max_chars: int = 1500) -> str:
    """Render KB hits as compact markdown for an analyst prompt."""
    if not hits:
        return ""
    lines = [f"# {header}", ""]
    for h in hits:
        title = h["title"] or "(untitled report)"
        loc = f" · p.{h['page'] + 1}" if isinstance(h.get("page"), int) else ""
        sym = f" · {h['symbol']}" if h.get("symbol") else ""
        lines.append(f"## {title}{sym}{loc}  _(relevance {h['score']:.2f})_")
        text = h["text"]
        if len(text) > max_chars:
            text = text[:max_chars] + " …"
        lines.append(text)
        if h.get("pdf_url"):
            lines.append(f"<{h['pdf_url']}>")
        lines.append("")
    return "\n".join(lines).strip()
