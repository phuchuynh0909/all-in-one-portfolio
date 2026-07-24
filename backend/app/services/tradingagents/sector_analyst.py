"""Sector analyst — a standalone report section for TradingAgents.

The vendored framework ships market / news / sentiment / fundamentals analysts.
This adds a *sector* view without touching the pristine vendor: the runner calls
``run_sector_analyst`` after the graph completes and surfaces the result as its own
``sector`` report section.

It analyses two things the other analysts don't:

  * **Sector metrics** — the ticker's sector (from ``stock_symbol`` sector levels)
    and that sector's fundamental + momentum metrics (``sector`` table).
  * **Sector news from the knowledge base** — a semantic search over embedded
    research reports (``kb_search``), falling back to a live web search only when
    the KB has no match.

The gathered context is handed to the quick-thinking LLM (same one the analysts
use) to produce a concise sector assessment. Everything is best-effort: any data
gap degrades to a note rather than failing the run.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

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
    """Assemble the sector data block (sector metrics + KB/web sector news)."""
    from app.db.base import SessionLocal
    from . import kb_search, web_search as ws

    sym = symbol.upper()
    parts: list[str] = []
    sector_label = sym

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
            f"SECTOR_MAPPING_UNAVAILABLE: {sym} is not mapped to a sector in "
            f"stock_symbol. Assess the sector from the research notes below; do "
            f"not fabricate metrics."
        )
    else:
        sector = info["sector"]
        sector_label = getattr(sector, "name", None) or f"sector #{info['sector_id']}"
        parts.append(f"# Sector: {sector_label} (level {info['level']})")
        metric_lines = [
            f"- {label}: {_fmt(getattr(sector, attr, None))}"
            for attr, label in _SECTOR_METRICS
        ]
        parts.append("## Sector metrics\n" + "\n".join(metric_lines))

    # Sector news — knowledge base first, then web fallback.
    kb_hits = kb_search.search(
        f"{sector_label} sector industry outlook trends drivers risks Vietnam"
    )
    if kb_hits:
        parts.append(kb_search.format_hits("Sector research from knowledge base", kb_hits))
    elif ws.web_search_enabled():
        parts.append(
            ws.search_and_format(
                f"{sector_label} Vietnam sector industry outlook stock",
                max_results=ws.DEFAULT_MAX_RESULTS,
                days=30,
            )
        )

    return "\n\n".join(parts)


_SYSTEM_PROMPT = (
    "You are a sector analyst for the Vietnamese stock market. Using ONLY the "
    "provided sector data (metrics and research notes), write a concise assessment "
    "of the sector {symbol} belongs to and how {symbol} is positioned within it. "
    "Cover: (1) sector health and momentum, (2) sector-level catalysts and risks "
    "from the research notes, (3) what this implies for {symbol}. Do not invent "
    "figures or headlines beyond the data. End with a one-line sector stance for "
    "{symbol}: Tailwind / Neutral / Headwind. Write in {language}."
)


def run_sector_analyst(symbol: str, trade_date: str, llm: Any, language: str = "English") -> str:
    """Produce the sector report section (markdown). Never raises."""
    sym = symbol.upper()
    try:
        context = build_sector_context(sym, trade_date)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Sector context build failed for %s", sym)
        return f"SECTOR_ANALYSIS_UNAVAILABLE: failed to gather sector data ({exc})."

    system = _SYSTEM_PROMPT.format(symbol=sym, language=language)
    user = (
        f"Sector data for {sym} as of {trade_date}:\n\n{context}\n\n"
        f"Write the sector assessment now."
    )
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        content = getattr(resp, "content", None) or str(resp)
        return str(content).strip()
    except Exception as exc:  # noqa: BLE001 — fall back to raw context, never fail the run
        logger.warning("Sector LLM call failed for %s: %s", sym, exc)
        return (
            f"# Sector context for {sym} (raw — LLM synthesis unavailable: {exc})\n\n"
            f"{context}"
        )
