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

  * **Industry** — resolved from the sieucophieu.vn stock API, which is more
    current than our own ``stock_symbol`` sector mapping and also carries the
    ticker's exchange / rating / trailing returns.
  * **Sector metrics** — the ticker's sector (from ``stock_symbol`` sector levels)
    and that sector's fundamental + momentum metrics (``sector`` table).
  * **Sector news from the knowledge base** — a semantic search over embedded
    research reports (``kb_search``), falling back to a live web search only when
    the KB has no match.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Public stock-profile API used to resolve a ticker's industry. Override the base
# with TRADINGAGENTS_STOCK_API_URL (must accept ``{base}/{SYMBOL}/``).
_STOCK_API_URL = os.getenv(
    "TRADINGAGENTS_STOCK_API_URL", "https://sieucophieu.vn/api/v1/stock/stocks"
)
_STOCK_API_TIMEOUT = float(os.getenv("TRADINGAGENTS_STOCK_API_TIMEOUT", "8"))

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

# Sector-table metrics worth showing the LLM, with human labels.
_SECTOR_METRICS: tuple[tuple[str, str], ...] = (
    ("smg", "SMG score"),
    ("dif", "Change %"),
    ("dif_w", "Change % (1w)"),
    ("dif_m", "Change % (1m)"),
    ("dif_3m", "Change % (3m)"),
    ("pe_d", "P/E"),
    ("pb_d", "P/B"),
    ("roe_ttm", "ROE ttm"),
    ("roa_ttm", "ROA ttm"),
    ("lnst_yoy_ttm", "Net-profit YoY ttm"),
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


def _resolve_sector(db, symbol: str) -> Optional[dict[str, Any]]:
    """Find the ticker's most specific sector (prefer level 2, then 1).

    Returns ``{level, sector_id, sector}`` or None if the symbol isn't mapped to
    any sector.
    """
    from app.db.models.market import StockSymbol
    from app.services import sector_service

    row = (
        db.query(StockSymbol)
        .filter(StockSymbol.symbol == symbol.upper())
        .first()
    )
    if row is None:
        return None

    # Prefer the finer level-2 grouping when present, else level-1.
    for level, sector_id in ((2, row.id_sector_level_2), (1, row.id_sector_level_1)):
        if sector_id is None:
            continue
        sector = sector_service.get_sector(db, int(sector_id), level)
        if sector is None:
            continue
        return {"level": level, "sector_id": int(sector_id), "sector": sector}
    return None


def build_sector_context(symbol: str, trade_date: str) -> str:
    """Assemble the sector data block (industry + sector metrics + sector news).

    Returned as markdown for the News Analyst to reason over. Never raises.
    """
    from app.db.base import SessionLocal
    from . import kb_search, web_search as ws

    sym = symbol.upper()
    parts: list[str] = []

    # Industry from the stock API — the preferred label; also gives ticker-level
    # returns/rating that show how the stock sits against its industry.
    profile = fetch_stock_profile(sym)
    industry = _industry_label(profile)
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

    # Sector metrics still come from our own sector table (the API has no
    # aggregate P/E, ROE, SMG …), keyed off the stock_symbol sector mapping.
    db = SessionLocal()
    try:
        info = _resolve_sector(db, sym)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sector lookup failed for %s: %s", sym, exc)
        info = None
    finally:
        db.close()

    if info is None:
        parts.append(
            f"SECTOR_METRICS_UNAVAILABLE: {sym} is not mapped to a sector in "
            f"stock_symbol. Assess the sector from the industry and research "
            f"notes below; do not fabricate metrics."
        )
    else:
        sector = info["sector"]
        sector_name = getattr(sector, "name", None) or f"sector #{info['sector_id']}"
        metric_lines = [
            f"- {label}: {_fmt(getattr(sector, attr, None))}"
            for attr, label in _SECTOR_METRICS
        ]
        parts.append(
            f"## Sector metrics — {sector_name} (level {info['level']})\n"
            + "\n".join(metric_lines)
        )
        industry = industry or sector_name

    # Sector news — knowledge base first, then web fallback.
    search_label = industry or sym
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
