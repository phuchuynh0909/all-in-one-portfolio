"""Vietnamese-market data adapter for the vendored TradingAgents framework.

TradingAgents ships US-market vendors (yfinance, Alpha Vantage, FRED, Reddit,
Polymarket). Every analyst tool ultimately routes through
``tradingagents.dataflows.interface.route_to_vendor(method, *args)``, which
dispatches by method name to a vendor implementation. This module implements a
single ``portfolio`` vendor backed by *this* platform's data:

  * OHLCV + technical indicators — ClickHouse ``ohlc_eod`` via
    ``app.services.stock_service._load_delta_stocks`` + ``stockstats``.
  * Company news — wichart research reports (ClickHouse metadata + DeltaLake
    report bodies/summaries via ``WichartReportStore``).

Data we have no Vietnamese-market source for (global/macro news, insider
filings, prediction markets) returns an explicit ``*_UNAVAILABLE`` sentinel so
the agents report "unavailable" instead of fabricating values.

``runner.register_vn_vendor()`` wires these functions into TradingAgents'
dispatch table and patches the verification-snapshot loader. The output formats
here deliberately mirror the yfinance vendor's, because the analyst prompts were
written against those shapes.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Core OHLCV loader (shared by price, indicator, and snapshot tools)
# ---------------------------------------------------------------------------

_RENAME = {
    "date": "Date",
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
}
_OHLCV_COLS = ["Date", "Open", "High", "Low", "Close", "Volume"]


def _load_ohlcv_frame(
    symbol: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> pd.DataFrame:
    """Load one symbol's daily OHLCV as a capitalized-column frame.

    Returns an empty DataFrame when the symbol has no rows. Columns:
    ``Date, Open, High, Low, Close, Volume`` (``Date`` as datetime, ascending),
    which is exactly the shape ``stockstats.wrap`` and the vendor formatters
    expect.
    """
    from app.services.stock_service import _load_delta_stocks

    raw = _load_delta_stocks(symbols=[symbol.upper()], start=start, end=end)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=_OHLCV_COLS)

    df = raw.rename(columns=_RENAME)
    keep = [c for c in _OHLCV_COLS if c in df.columns]
    df = df[keep].copy()
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    return df


def load_ohlcv(symbol: str, curr_date: str) -> pd.DataFrame:
    """Drop-in replacement for ``stockstats_utils.load_ohlcv``.

    Same signature/contract as the framework helper (used by the verification
    snapshot): a look-ahead-free frame ending on or before ``curr_date``. Raises
    ``NoMarketDataError`` when nothing is available so the router emits its
    standard sentinel.
    """
    from tradingagents.dataflows.symbol_utils import NoMarketDataError

    curr = pd.to_datetime(curr_date)
    start = (curr - pd.DateOffset(years=2)).to_pydatetime()
    df = _load_ohlcv_frame(symbol, start=start, end=curr.to_pydatetime())
    if not df.empty:
        df = df[df["Date"] <= curr].reset_index(drop=True)
    if df.empty:
        raise NoMarketDataError(
            symbol, symbol.upper(), f"no OHLCV rows on or before {curr_date}"
        )
    return df


# ---------------------------------------------------------------------------
# core_stock_apis : get_stock_data
# ---------------------------------------------------------------------------


def get_stock_data(symbol: str, start_date: str, end_date: str) -> str:
    """OHLCV price history for a ticker over a date range (CSV, like yfinance)."""
    from tradingagents.dataflows.symbol_utils import NoMarketDataError

    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    df = _load_ohlcv_frame(symbol, start=start, end=end)
    if df.empty:
        raise NoMarketDataError(
            symbol, symbol.upper(), f"no rows between {start_date} and {end_date}"
        )

    out = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        if col in out.columns:
            out[col] = out[col].round(2)
    out = out.set_index("Date")
    csv_string = out.to_csv()

    header = (
        f"# Stock data for {symbol.upper()} (Vietnam equity) "
        f"from {start_date} to {end_date}\n"
        f"# Total records: {len(out)}\n"
        f"# Source: platform ClickHouse ohlc_eod (VND, split/dividend adjusted)\n\n"
    )
    return header + csv_string


# ---------------------------------------------------------------------------
# technical_indicators : get_indicators
# ---------------------------------------------------------------------------

# Same vocabulary + guidance the yfinance vendor exposes, so the market
# analyst's prompt (which names these exact indicators) keeps working.
_IND_DESCRIPTIONS: dict[str, str] = {
    "close_50_sma": (
        "50 SMA: A medium-term trend indicator. Usage: Identify trend direction "
        "and serve as dynamic support/resistance. Tips: It lags price; combine "
        "with faster indicators for timely signals."
    ),
    "close_200_sma": (
        "200 SMA: A long-term trend benchmark. Usage: Confirm overall market "
        "trend and identify golden/death cross setups. Tips: It reacts slowly; "
        "best for strategic trend confirmation."
    ),
    "close_10_ema": (
        "10 EMA: A responsive short-term average. Usage: Capture quick shifts in "
        "momentum and potential entry points. Tips: Prone to noise in choppy "
        "markets; use alongside longer averages to filter false signals."
    ),
    "macd": (
        "MACD: Computes momentum via differences of EMAs. Usage: Look for "
        "crossovers and divergence as signals of trend changes. Tips: Confirm "
        "with other indicators in low-volatility or sideways markets."
    ),
    "macds": (
        "MACD Signal: An EMA smoothing of the MACD line. Usage: Use crossovers "
        "with the MACD line to trigger trades. Tips: Part of a broader strategy "
        "to avoid false positives."
    ),
    "macdh": (
        "MACD Histogram: Shows the gap between the MACD line and its signal. "
        "Usage: Visualize momentum strength and spot divergence early. Tips: Can "
        "be volatile; complement with additional filters."
    ),
    "rsi": (
        "RSI: Measures momentum to flag overbought/oversold conditions. Usage: "
        "Apply 70/30 thresholds and watch for divergence to signal reversals. "
        "Tips: In strong trends RSI can stay extreme; cross-check with trend."
    ),
    "boll": (
        "Bollinger Middle: A 20 SMA basis for Bollinger Bands. Usage: Dynamic "
        "benchmark for price movement. Tips: Combine with the bands to spot "
        "breakouts or reversals."
    ),
    "boll_ub": (
        "Bollinger Upper Band: ~2 std devs above the middle line. Usage: Signals "
        "potential overbought conditions and breakout zones. Tips: Prices may "
        "ride the band in strong trends."
    ),
    "boll_lb": (
        "Bollinger Lower Band: ~2 std devs below the middle line. Usage: "
        "Indicates potential oversold conditions. Tips: Confirm to avoid false "
        "reversal signals."
    ),
    "atr": (
        "ATR: Averages true range to measure volatility. Usage: Set stop-loss "
        "levels and size positions to current volatility. Tips: Reactive; use "
        "within a broader risk framework."
    ),
    "vwma": (
        "VWMA: A moving average weighted by volume. Usage: Confirm trends by "
        "integrating price with volume. Tips: Volume spikes can skew it."
    ),
    "mfi": (
        "MFI: Money Flow Index uses price and volume to measure buying/selling "
        "pressure. Usage: Overbought >80 / oversold <20 and trend confirmation. "
        "Tips: Divergence vs price can flag reversals."
    ),
}


def get_indicators(
    symbol: str,
    indicator: str,
    curr_date: str,
    look_back_days: int = 30,
) -> str:
    """One technical indicator's values over a look-back window (stockstats)."""
    from stockstats import wrap
    from dateutil.relativedelta import relativedelta

    indicator = indicator.strip().lower()
    if indicator not in _IND_DESCRIPTIONS:
        raise ValueError(
            f"Indicator {indicator} is not supported. Please choose from: "
            f"{list(_IND_DESCRIPTIONS.keys())}"
        )

    # Frame ending on/before curr_date (raises NoMarketDataError if empty).
    df = load_ohlcv(symbol, curr_date)

    # wrap() mutates its argument in place, so hand it a copy and keep df intact
    # for the (capitalized) Date column — stockstats lowercases columns.
    work = df.copy()
    stock_df = wrap(work)
    series = stock_df[indicator]  # triggers stockstats calculation
    dates = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d").tolist()
    values = dict(zip(dates, list(series.values)))

    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = curr_dt - relativedelta(days=look_back_days)

    lines = []
    cursor = curr_dt
    while cursor >= before:
        key = cursor.strftime("%Y-%m-%d")
        value = values.get(key)
        if value is None or pd.isna(value):
            rendered = "N/A: Not a trading day (weekend or holiday)"
        elif isinstance(value, float):
            rendered = f"{value:.4f}"
        else:
            rendered = str(value)
        lines.append(f"{key}: {rendered}")
        cursor = cursor - relativedelta(days=1)

    return (
        f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {curr_date}:\n\n"
        + "\n".join(lines)
        + "\n\n"
        + _IND_DESCRIPTIONS[indicator]
    )


# ---------------------------------------------------------------------------
# news_data : get_news (company research reports)
# ---------------------------------------------------------------------------

_MAX_REPORTS = 8
_MAX_SUMMARIES = 4  # cap the (slower) DeltaLake body lookups per call


def _report_summary(report_id) -> str | None:
    """Best-effort research-report body/summary from the wichart detail store."""
    try:
        from app.stores.raw_wichart_report import WichartReportStore

        detail = WichartReportStore().get_detail(int(report_id))
    except Exception as exc:  # noqa: BLE001 — enrichment only, never fail the tool
        logger.debug("wichart detail lookup failed for %s: %s", report_id, exc)
        return None
    if detail is None or getattr(detail, "empty", True):
        return None

    row = detail.iloc[0]
    for field in ("llm_summary", "clean_content"):
        value = row.get(field) if hasattr(row, "get") else None
        if isinstance(value, str) and value.strip():
            text = value.strip()
            return text[:1200] + (" …" if len(text) > 1200 else "")
    return None


def _report_section(sym: str, start_date: str, end_date: str) -> str | None:
    """Curated wichart research-report section, or None when unavailable/empty.

    Best-effort: a ClickHouse hiccup must not prevent the web-search enrichment
    in ``get_news`` from still reaching the analyst.
    """
    try:
        from app.services.report_service import _query_raw_reports

        meta = _query_raw_reports(symbol=sym)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Report query failed for %s: %s", sym, exc)
        return None

    if meta is None or meta.empty:
        return None

    meta = meta.copy()
    meta["ngaykn"] = pd.to_datetime(meta["ngaykn"], errors="coerce")
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)

    in_range = meta[(meta["ngaykn"] >= start) & (meta["ngaykn"] <= end)]
    note = ""
    if in_range.empty:
        # Reports are sparse; fall back to the most recent ones before end_date
        # so the analyst still has context rather than an empty result.
        in_range = meta[meta["ngaykn"] <= end]
        note = (
            " (No reports fell strictly within the requested window; showing the "
            "most recent prior reports for context.)"
        )
    rows = in_range.sort_values("ngaykn", ascending=False).head(_MAX_REPORTS)
    if rows.empty:
        return None

    parts = [f"# Research reports for {sym} ({start_date} to {end_date}){note}", ""]
    summaries_fetched = 0
    for _, row in rows.iterrows():
        date_str = (
            row["ngaykn"].strftime("%Y-%m-%d") if pd.notna(row["ngaykn"]) else "n/a"
        )
        title = (row.get("tenbaocao") or "").strip() or "(untitled report)"
        source = (row.get("nguon") or "").strip() or "unknown source"
        industry = (row.get("rsnganh") or "").strip()
        parts.append(f"## {date_str} — {title}")
        meta_line = f"Source: {source}"
        if industry:
            meta_line += f" · Industry: {industry}"
        parts.append(meta_line)

        if summaries_fetched < _MAX_SUMMARIES:
            summary = _report_summary(row.get("id"))
            summaries_fetched += 1
            if summary:
                parts.append("")
                parts.append(summary)
        parts.append("")
    return "\n".join(parts)


def get_news(ticker: str, start_date: str, end_date: str) -> str:
    """Company news for the News & Sentiment analysts, knowledge-base first.

    Tiered so we prefer our own curated research over the open web:
      1. **Knowledge base** — semantic search over embedded wichart research
         reports in Qdrant, filtered to this ticker (``kb_search``).
      2. If the KB has no match, the curated report *metadata* (ClickHouse) is a
         cheap secondary internal source.
      3. Only when we have no internal signal at all do we fall back to a live
         **web search**.
    """
    from . import kb_search, web_search as ws

    sym = ticker.upper()

    # Tier 1: knowledge base (embedded research reports).
    kb_hits = kb_search.search(
        f"{sym} company news outlook earnings valuation risks catalysts",
        symbols=[sym],
    )
    if kb_hits:
        body = kb_search.format_hits(f"Knowledge-base research for {sym}", kb_hits)
        note = (
            "Source: internal knowledge base (curated research reports). This is "
            "the primary company signal; base the assessment on it and do not "
            "fabricate headlines beyond what is shown."
        )
        return f"{body}\n\n{note}"

    # Tier 2: curated report metadata (reports that exist but aren't embedded yet).
    report_text = _report_section(sym, start_date, end_date)
    if report_text:
        return (
            f"{report_text}\n\n"
            "Source: curated research reports (no knowledge-base match). Base the "
            "assessment on this evidence; do not fabricate headlines."
        )

    # Tier 3: live web search fallback (no internal knowledge for this ticker).
    if ws.web_search_enabled():
        days = _lookback_days(start_date, end_date)
        web = ws.search_and_format(
            f"{sym} stock Vietnam news",
            max_results=ws.DEFAULT_MAX_RESULTS,
            days=days,
        )
        return (
            f"{web}\n\n"
            f"Source: live web search (no knowledge-base or research-report match "
            f"for {sym}). Do not fabricate headlines beyond what is shown."
        )

    return (
        f"No company news available for {sym} (no knowledge-base match, no research "
        f"reports, and web search disabled/unavailable). Do not fabricate headlines."
    )


def _lookback_days(start_date: str, end_date: str) -> int:
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        return max(1, (end - start).days)
    except Exception:  # noqa: BLE001
        return 7


# ---------------------------------------------------------------------------
# Sentinels — categories with no Vietnamese-market source configured
# ---------------------------------------------------------------------------


# Macro/market queries powering get_global_news. Override with
# TRADINGAGENTS_NEWS_QUERIES (comma-separated) — defaults blend Vietnam-market
# and global-macro angles.
_DEFAULT_NEWS_QUERIES = (
    "Vietnam stock market VN-Index news",
    "State Bank of Vietnam interest rate inflation",
    "Vietnam economy GDP export FDI outlook",
    "global markets Fed interest rates macro outlook",
)


def _news_queries() -> list[str]:
    raw = os.getenv("TRADINGAGENTS_NEWS_QUERIES")
    if raw:
        queries = [q.strip() for q in raw.split(",") if q.strip()]
        if queries:
            return queries
    return list(_DEFAULT_NEWS_QUERIES)


def get_global_news(curr_date=None, look_back_days=None, limit=None) -> str:
    """Macro/market news via live web search over a set of market queries."""
    from . import web_search as ws

    if not ws.web_search_enabled():
        return (
            "GLOBAL_NEWS_UNAVAILABLE: web search is disabled "
            "(TRADINGAGENTS_WEB_SEARCH=0). Base your assessment on company-level "
            "reports from get_news; do not fabricate macro headlines."
        )

    days = int(look_back_days) if look_back_days else 7
    per_query = int(limit) if limit else ws.DEFAULT_MAX_RESULTS
    # Spread the result budget across queries so one topic can't dominate.
    per_query = max(2, min(per_query, 5))

    sections = [f"# Global / macro & Vietnam-market news (as of {curr_date or 'now'})", ""]
    for query in _news_queries():
        sections.append(ws.search_and_format(query, max_results=per_query, days=days))
        sections.append("")
    sections.append(
        "Synthesize the macro backdrop from these results; cite concrete "
        "headlines and do not invent figures."
    )
    return "\n".join(sections)


def get_insider_transactions(ticker: str) -> str:
    return (
        f"INSIDER_DATA_UNAVAILABLE: No insider-transaction feed is configured for "
        f"Vietnamese equities ({str(ticker).upper()}). Do not fabricate filings."
    )


def get_macro_indicators(*args, **kwargs) -> str:
    return (
        "MACRO_DATA_UNAVAILABLE: No macro-indicator vendor (e.g. FRED) is "
        "configured for this Vietnamese-market deployment. Proceed without it; "
        "do not fabricate macro figures."
    )


def get_prediction_markets(*args, **kwargs) -> str:
    return (
        "PREDICTION_MARKETS_UNAVAILABLE: No prediction-market vendor is configured "
        "for this deployment. Proceed without event probabilities."
    )


# Method name -> VN implementation. Consumed by runner.register_vn_vendor().
VN_VENDOR_METHODS: dict[str, callable] = {
    "get_stock_data": get_stock_data,
    "get_indicators": get_indicators,
    "get_news": get_news,
    "get_global_news": get_global_news,
    "get_insider_transactions": get_insider_transactions,
    "get_macro_indicators": get_macro_indicators,
    "get_prediction_markets": get_prediction_markets,
}
