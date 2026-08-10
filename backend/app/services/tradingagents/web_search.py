"""Internet search for the TradingAgents news/sentiment analysts.

Powers the ``get_global_news`` (macro/market) and ``get_news`` (ticker-specific)
tools in ``vn_data`` with real web results. Two backends, tried in order:

  1. **Tavily** — used when ``TAVILY_API_KEY`` is set. An LLM-oriented search API
     with clean, dated results (recommended for quality).
  2. **DuckDuckGo** — keyless default via the ``ddgs`` package. No API key, good
     enough for headlines.

Everything degrades gracefully: if no backend is available or a call fails, the
callers fall back to a clear "search unavailable" note rather than crashing the
run. Disable entirely with ``TRADINGAGENTS_WEB_SEARCH=0``.
"""
from __future__ import annotations

import logging
import os
from typing import TypedDict

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = int(os.getenv("TRADINGAGENTS_SEARCH_MAX_RESULTS", "5"))
TAVILY_URL = "https://api.tavily.com/search"


class SearchResult(TypedDict):
    title: str
    url: str
    snippet: str
    date: str


def web_search_enabled() -> bool:
    return os.getenv("TRADINGAGENTS_WEB_SEARCH", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def active_backend() -> str:
    """Name of the backend that would serve a search now (for diagnostics)."""
    if not web_search_enabled():
        return "disabled"
    if os.getenv("TAVILY_API_KEY"):
        return "tavily"
    try:
        _import_ddgs()
        return "duckduckgo"
    except Exception:  # noqa: BLE001
        return "none"


def _timelimit(days: int | None) -> str | None:
    """Map a look-back window (days) to DuckDuckGo's timelimit codes."""
    if not days:
        return None
    if days <= 7:
        return "w"
    if days <= 31:
        return "m"
    return "y"


def _import_ddgs():
    """Return a DDGS class from ``ddgs`` (new) or ``duckduckgo_search`` (old)."""
    try:
        from ddgs import DDGS  # type: ignore

        return DDGS
    except Exception:  # noqa: BLE001
        from duckduckgo_search import DDGS  # type: ignore

        return DDGS


def _search_tavily(query: str, max_results: int, days: int | None) -> list[SearchResult]:
    import requests

    payload = {
        "api_key": os.environ["TAVILY_API_KEY"],
        "query": query,
        "max_results": max_results,
        "topic": "news",
        "search_depth": "basic",
    }
    if days:
        payload["days"] = days
    resp = requests.post(TAVILY_URL, json=payload, timeout=15)
    resp.raise_for_status()
    out: list[SearchResult] = []
    for r in resp.json().get("results", []):
        out.append(
            SearchResult(
                title=str(r.get("title", "")).strip(),
                url=str(r.get("url", "")).strip(),
                snippet=str(r.get("content", "")).strip(),
                date=str(r.get("published_date", "") or "").strip(),
            )
        )
    return out


def _search_ddg(query: str, max_results: int, days: int | None) -> list[SearchResult]:
    DDGS = _import_ddgs()
    timelimit = _timelimit(days)
    out: list[SearchResult] = []
    with DDGS() as ddgs:
        # Prefer the dated news endpoint; fall back to general text search.
        try:
            rows = list(ddgs.news(query, max_results=max_results, timelimit=timelimit))
        except Exception:  # noqa: BLE001
            rows = list(ddgs.text(query, max_results=max_results))
        for r in rows:
            out.append(
                SearchResult(
                    title=str(r.get("title", "")).strip(),
                    url=str(r.get("url") or r.get("href", "")).strip(),
                    snippet=str(r.get("body", "")).strip(),
                    date=str(r.get("date", "") or "").strip(),
                )
            )
    return out


def web_search(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    days: int | None = None,
) -> list[SearchResult]:
    """Run one web search. Raises RuntimeError if no backend is available."""
    if not web_search_enabled():
        raise RuntimeError("web search disabled (TRADINGAGENTS_WEB_SEARCH=0)")

    if os.getenv("TAVILY_API_KEY"):
        try:
            return _search_tavily(query, max_results, days)
        except Exception as exc:  # noqa: BLE001 — fall through to DDG
            logger.warning("Tavily search failed (%s); falling back to DuckDuckGo", exc)

    return _search_ddg(query, max_results, days)


def format_results(query: str, results: list[SearchResult]) -> str:
    """Render results as compact markdown for an analyst prompt."""
    if not results:
        return f"No web results for '{query}'."
    lines = [f"### Web results — {query}"]
    for r in results:
        if not (r["title"] or r["snippet"]):
            continue
        head = r["title"] or r["url"]
        if r["date"]:
            head = f"{r['date']} — {head}"
        lines.append(f"- **{head}**")
        if r["snippet"]:
            lines.append(f"  {r['snippet']}")
        if r["url"]:
            lines.append(f"  <{r['url']}>")
    return "\n".join(lines)


def search_and_format(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    days: int | None = None,
) -> str:
    """Search + render, returning a clear note (never raising) on failure."""
    try:
        results = web_search(query, max_results=max_results, days=days)
        return format_results(query, results)
    except Exception as exc:  # noqa: BLE001
        logger.warning("web_search failed for %r: %s", query, exc)
        return (
            f"WEB_SEARCH_UNAVAILABLE for '{query}': {exc}. "
            "Proceed without it; do not fabricate headlines."
        )
