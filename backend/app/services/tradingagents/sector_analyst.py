"""Sector context for the News Analyst.

Upstream TradingAgents ships four analysts (fundamentals / sentiment / news /
technical) — there is no sector role. Rather than bolting a fifth node onto the
pristine vendor, this module assembles a *sector data block* that
:func:`app.services.tradingagents.vn_data.get_news` appends to its output, so the
vendored News Analyst — whose brief is "monitors global news and macroeconomic
indicators, interpreting the impact of events on market conditions" — reasons
over sector context as part of its own report. No LLM call happens here; the
News Analyst's model does the synthesis.

Three sources, all best-effort (any gap degrades to a note, never an exception):

  * **Industry** — the industry/news-search label prefers the committed
    ``sector_map.json`` (a curated sieucophieu.vn sector taxonomy; see
    ``backend/scripts/build_sector_map.py``), falling back to the sieucophieu.vn
    stock API, then the DB ``stock_symbol`` sector name. The stock API is still
    read for the ticker's exchange / rating / trailing returns.
  * **Sector metrics** — per sector_map.json tag, merged from the sieucophieu
    industry APIs (``industry_strength`` [needs a token], ``industry_cashflow``,
    ``industry_spread``): relative strength, ROC, cashflow and breadth.
  * **Sector news from the knowledge base** — a semantic search over embedded
    research reports (``kb_search``), falling back to a live web search only when
    the KB has no match.
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Committed symbol -> [sector name, ...] map built from sieucophieu.vn stock
# lists (see backend/scripts/build_sector_map.py). Preferred over the stock-API
# industry and the DB sector name for the industry/news-search label because it
# is a curated, VN-market sector taxonomy. sector_analyst.py lives at
# app/services/tradingagents/, so the file is two parents up.
_SECTOR_MAP_PATH = Path(__file__).resolve().parents[2] / "sector_map.json"


@lru_cache(maxsize=1)
def _load_sector_map() -> dict[str, list[str]]:
    """Load the committed symbol->sectors map. Empty dict on any failure."""
    try:
        with open(_SECTOR_MAP_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {str(k).upper(): list(v) for k, v in data.items()}
    except Exception as exc:  # noqa: BLE001 — optional enrichment, never fatal
        logger.warning("sector_map.json unavailable at %s: %s", _SECTOR_MAP_PATH, exc)
        return {}


def sector_tags(symbol: str) -> list[str]:
    """Sector name(s) for a ticker from the committed map; [] if unmapped."""
    return list(_load_sector_map().get(symbol.upper(), []))


def _mapped_label(symbol: str) -> Optional[str]:
    """The map's sector name(s) as one label (multi-tags joined), or None."""
    tags = sector_tags(symbol)
    return " / ".join(tags) if tags else None


def _preferred_label(
    symbol: str,
    profile_industry: Optional[str],
    db_sector_name: Optional[str],
) -> str:
    """Industry/news-search label: map > stock-API industry > DB name > symbol."""
    return (
        _mapped_label(symbol)
        or (profile_industry or None)
        or (db_sector_name or None)
        or symbol.upper()
    )

# Public stock-profile API used to resolve a ticker's industry. Override the base
# with TRADINGAGENTS_STOCK_API_URL (must accept ``{base}/{SYMBOL}/``).
_STOCK_API_URL = os.getenv(
    "TRADINGAGENTS_STOCK_API_URL", "https://sieucophieu.vn/api/v1/stock/stocks"
)
_STOCK_API_TIMEOUT = float(os.getenv("TRADINGAGENTS_STOCK_API_TIMEOUT", "8"))

# Industry-statistics endpoints (strength / cashflow / spread) all key on the
# sieucophieu sector *name* — the same names sector_map.json stores — and each
# returns every sector in one call, so we fetch once per process and look up by
# name. Override the base with TRADINGAGENTS_INDUSTRY_API_URL.
_INDUSTRY_API_URL = os.getenv(
    "TRADINGAGENTS_INDUSTRY_API_URL", "https://sieucophieu.vn/api/v1/stock"
)
_INDUSTRY_API_TIMEOUT = float(os.getenv("TRADINGAGENTS_INDUSTRY_API_TIMEOUT", "8"))
# industry_strength requires auth (401 without); cashflow + spread are public.
# Token is read from the environment, never hardcoded; when absent we skip only
# the strength fields and still serve cashflow + spread.
_SIEUCOPHIEU_TOKEN = os.getenv("TRADINGAGENTS_SIEUCOPHIEU_TOKEN", "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzg4Njc1MDM2LCJpYXQiOjE3ODc0NjU0MzYsImp0aSI6IjI3NTk0OTk0ZjM3MjRhOTViMDg3MjM4ZjU0M2FkNjAwIiwidXNlcl9pZCI6NTU0Mn0.PeBWP9j3in854Wp4vd8j7RfoWoDxO1xb4ucSiBpuI5o")
_INDUSTRY_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

# Merged metric fields shown per sector, in display order, with human labels.
_INDUSTRY_METRICS: tuple[tuple[str, str], ...] = (
    ("rs_relative", "RS relative"),
    ("rs_short", "RS (short-term)"),
    ("rs_mid", "RS (mid-term)"),
    ("roc", "ROC %"),
    ("cashflow", "Cashflow"),
    ("cashflow_change_percent", "Cashflow change %"),
    ("up_percent", "Advancers %"),
    ("down_percent", "Decliners %"),
)

# Profiles are static within a run; cache so the News Analyst calling get_news
# repeatedly doesn't re-hit the API. Maps SYMBOL -> profile dict (or None on miss).
_profile_cache: dict[str, Optional[dict[str, Any]]] = {}

# Ticker-level fields from the stock API worth showing alongside the sector view,
# with human labels. These frame how the stock sits against its industry.
_PROFILE_FIELDS: tuple[tuple[str, str], ...] = (
    ("exchange", "Exchange"),
    ("stock_rating", "Stock rating"),
    ("delta_in_week", "Return (1w)"),
    ("delta_in_month", "Return (1m)"),
    ("delta_in_year", "Return (1y)"),
    ("foreign_percent", "Foreign ownership"),
)


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def fetch_stock_profile(symbol: str) -> Optional[dict[str, Any]]:
    """Fetch the ticker's profile (industry, exchange, returns) from the stock API.

    Cached per symbol for the process lifetime. Returns None when the symbol is
    unknown or the API is unreachable — callers fall back to the DB mapping.
    """
    sym = symbol.upper()
    if sym in _profile_cache:
        return _profile_cache[sym]

    profile: Optional[dict[str, Any]] = None
    try:
        import requests

        resp = requests.get(
            f"{_STOCK_API_URL.rstrip('/')}/{sym}/",
            headers={"Accept": "application/json"},
            timeout=_STOCK_API_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("symbol"):
            profile = data
    except Exception as exc:  # noqa: BLE001 — optional enrichment, never fatal
        logger.warning("Stock profile lookup failed for %s: %s", sym, exc)

    _profile_cache[sym] = profile
    return profile


def _industry_label(profile: Optional[dict[str, Any]]) -> Optional[str]:
    """Human industry name from a stock profile, preferring the English name."""
    if not profile:
        return None
    en = str(profile.get("industry_en") or "").strip()
    vi = str(profile.get("industry") or "").strip()
    if en and vi and en.lower() != vi.lower():
        return f"{en} ({vi})"
    return en or vi or None


def _fetch_industry_json(endpoint: str, *, use_token: bool) -> Any:
    """GET one industry endpoint's raw JSON. None on any failure/missing token."""
    headers = {"Accept": "application/json", "User-Agent": _INDUSTRY_UA}
    if use_token:
        if not _SIEUCOPHIEU_TOKEN:
            logger.info(
                "industry %s skipped: TRADINGAGENTS_SIEUCOPHIEU_TOKEN not set",
                endpoint,
            )
            return None
        headers["Authorization"] = f"Bearer {_SIEUCOPHIEU_TOKEN}"
    try:
        import requests

        resp = requests.get(
            f"{_INDUSTRY_API_URL.rstrip('/')}/{endpoint}/",
            headers=headers,
            timeout=_INDUSTRY_API_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001 — optional enrichment, never fatal
        logger.warning("industry %s fetch failed: %s", endpoint, exc)
        return None


def _industry_rows(payload: Any) -> list[dict[str, Any]]:
    """Normalize a payload that is either ``[...]`` or ``{"data": [...]}``."""
    if isinstance(payload, dict):
        payload = payload.get("data")
    return [r for r in payload if isinstance(r, dict)] if isinstance(payload, list) else []


def _build_industry_metrics() -> dict[str, dict[str, Any]]:
    """Merge the three industry endpoints into ``sector name -> metrics``.

    Strength (needs token) supplies the RS fields; cashflow (public) supplies
    ROC + cashflow; spread (public) supplies breadth. Any endpoint that fails
    simply contributes nothing.
    """
    metrics: dict[str, dict[str, Any]] = {}

    def _bucket(name: Any) -> Optional[dict[str, Any]]:
        if not name:
            return None
        return metrics.setdefault(str(name), {})

    def _copy(row: dict[str, Any], bucket: dict[str, Any], keys, overwrite: bool):
        for key in keys:
            value = row.get(key)
            if value is None:
                continue
            if overwrite or key not in bucket:
                bucket[key] = value

    for row in _industry_rows(_fetch_industry_json("industry_strength", use_token=True)):
        bucket = _bucket(row.get("name"))
        if bucket is not None:
            _copy(row, bucket, ("rs_short", "rs_mid", "rs_relative"), overwrite=True)

    for row in _industry_rows(_fetch_industry_json("industry_cashflow", use_token=False)):
        bucket = _bucket(row.get("stock_list_name"))
        if bucket is not None:
            _copy(row, bucket, ("roc", "cashflow", "cashflow_change_percent"), True)
            # RS also ships here; keep it only if strength didn't provide it.
            _copy(row, bucket, ("rs_short", "rs_mid", "rs_relative"), overwrite=False)

    for row in _industry_rows(_fetch_industry_json("industry_spread", use_token=False)):
        bucket = _bucket(row.get("name"))
        if bucket is not None:
            _copy(row, bucket, ("up_percent", "down_percent"), overwrite=True)
            _copy(row, bucket, ("roc",), overwrite=False)

    return metrics


@lru_cache(maxsize=1)
def _load_industry_metrics() -> dict[str, dict[str, Any]]:
    """Cached per-process ``sector name -> metrics`` from the industry APIs."""
    return _build_industry_metrics()


def _render_sector_metrics(name: str, metrics: Optional[dict[str, Any]]) -> str:
    """Markdown metrics block for one sector, or an explicit unavailable note."""
    if not metrics:
        return (
            f"SECTOR_METRICS_UNAVAILABLE: no industry metrics for '{name}' from the "
            f"sieucophieu industry APIs. Do not fabricate metrics."
        )
    lines = [
        f"- {label}: {_fmt(metrics.get(key))}"
        for key, label in _INDUSTRY_METRICS
        if metrics.get(key) is not None
    ]
    return f"## Sector metrics — {name}\n" + "\n".join(lines)


def build_sector_context(symbol: str, trade_date: str) -> str:
    """Assemble the sector data block (industry + sector metrics + sector news).

    Returned as markdown for the News Analyst to reason over. Never raises.
    """
    from . import kb_search, web_search as ws

    sym = symbol.upper()
    parts: list[str] = []

    # Industry label: prefer the committed sector_map.json (curated VN-market
    # taxonomy), then the stock-API industry, then the DB sector name (resolved
    # below), then the symbol. The stock API is still fetched for the ticker-level
    # returns/rating that show how the stock sits against its industry.
    profile = fetch_stock_profile(sym)
    industry = _mapped_label(sym) or _industry_label(profile)
    if profile:
        header = f"# Industry: {industry}" if industry else f"# {sym} profile"
        profile_lines = [
            f"- {label}: {_fmt(profile.get(key))}"
            for key, label in _PROFILE_FIELDS
            if profile.get(key) is not None
        ]
        parts.append(header)
        if profile_lines:
            parts.append(f"## {sym} vs industry\n" + "\n".join(profile_lines))

    # Sector metrics come from the sieucophieu industry APIs (strength /
    # cashflow / spread), keyed by the ticker's sector_map.json tag(s). A symbol
    # with several tags gets one metrics block per tag.
    tags = sector_tags(sym)
    if not tags:
        parts.append(
            f"SECTOR_METRICS_UNAVAILABLE: {sym} is not mapped to a sector in "
            f"sector_map.json. Assess the sector from the industry and research "
            f"notes below; do not fabricate metrics."
        )
    else:
        metrics_by_name = _load_industry_metrics()
        for tag in tags:
            parts.append(_render_sector_metrics(tag, metrics_by_name.get(tag)))

    # Sector news — knowledge base first, then web fallback.
    search_label = industry or sym.upper()
    kb_hits = kb_search.search(
        f"{search_label} sector industry outlook trends drivers risks Vietnam"
    )
    if kb_hits:
        parts.append(kb_search.format_hits("Sector research from knowledge base", kb_hits))
    elif ws.web_search_enabled():
        parts.append(
            ws.search_and_format(
                f"{search_label} Vietnam sector industry outlook stock",
                max_results=ws.DEFAULT_MAX_RESULTS,
                days=30,
            )
        )

    return "\n\n".join(parts)


def build_sector_section(symbol: str, trade_date: str) -> str:
    """The sector block as appended to ``get_news`` output. Never raises."""
    sym = symbol.upper()
    try:
        context = build_sector_context(sym, trade_date)
    except Exception as exc:  # noqa: BLE001 — must never break the news tool
        logger.exception("Sector context build failed for %s", sym)
        return (
            f"SECTOR_CONTEXT_UNAVAILABLE: failed to gather sector data ({exc}). "
            f"Proceed on company news alone; do not fabricate sector figures."
        )
    if not context.strip():
        return ""
    return (
        f"# Sector context for {sym}\n\n{context}\n\n"
        f"Cover the sector in your report: sector health and momentum, "
        f"sector-level catalysts and risks from the notes above, and how {sym} "
        f"is positioned within it. Do not invent figures beyond this data."
    )
