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
  * Fundamentals — ruatichsan financial statements plus 24hmoney's valuation and
    ownership snapshot (``money24h_client``).
  * Macro indicators — wichart's xbrain-news macro feed
    (``wichart_news_client``): rates, FX, SBV operations, CPI, GDP, trade.

Data we have no Vietnamese-market source for (US/global macro series, insider
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
from typing import Any

import pandas as pd

from .utils import fmt_billion, fmt_count, fmt_ratio, iso_day, lookback_days

logger = logging.getLogger(__name__)

# ===========================================================================
# Configuration & constants
#
# Every tunable (the TRADINGAGENTS_* environment variables) and every static
# table the tools below read lives here, grouped in the order the file uses
# them: OHLCV frames → indicators → company news → macro news → macro
# indicators → fundamentals. Dispatch tables that point at functions
# (_CUSTOM_INDICATORS, VN_VENDOR_METHODS) and the process-level caches stay
# beside the code they serve — those are state, not configuration.
# ===========================================================================

# ──────────────────────────────────────────────────────────────────────────
# OHLCV frames
# ──────────────────────────────────────────────────────────────────────────
# ClickHouse hands back lowercase columns; the framework's formatters and
# ``stockstats.wrap`` both expect the capitalized yfinance spelling.
_RENAME = {
    "date": "Date",
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
}
_OHLCV_COLS = ["Date", "Open", "High", "Low", "Close", "Volume"]

# ──────────────────────────────────────────────────────────────────────────
# Technical indicators
# ──────────────────────────────────────────────────────────────────────────
# Anchor-search window for the anchored VWAP, and the KAMA period — both feed
# the platform's own indicator implementations (see _CUSTOM_INDICATORS), so the
# analyst's numbers match the lines the charts draw. load_ohlcv's 2-year frame
# leaves room for a 200-bar anchor search across the whole look-back window.
_VWAP_WINDOW = int(os.getenv("TRADINGAGENTS_VWAP_WINDOW", "200"))
_KAMA_PERIOD = int(os.getenv("TRADINGAGENTS_KAMA_PERIOD", "10"))
# The market leg of the `regime` indicator (the ticker's own leg is the ticker).
_REGIME_MARKET_SYMBOL = os.getenv("TRADINGAGENTS_REGIME_MARKET", "VNINDEX")

# Same vocabulary + guidance the yfinance vendor exposes, so the market
# analyst's prompt (which names these exact indicators) keeps working, plus the
# extras below that only this vendor serves (see _CUSTOM_INDICATORS).
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
    "kama": (
        f"KAMA: Kaufman's Adaptive Moving Average ({_KAMA_PERIOD}-period, in "
        "VND). Speeds up in trending markets and flattens in noise, so it "
        "whipsaws far less than a fixed-length MA. Usage: Trend direction and "
        "dynamic support/resistance; a flat KAMA means the market is ranging. "
        "Tips: It adapts, it does not lead — do not expect early reversal calls."
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
        "MFI: Money Flow Index (14) uses price and volume to measure "
        "buying/selling pressure, scaled 0-100. Usage: Overbought >80 / oversold "
        "<20 and trend confirmation. Tips: Divergence vs price can flag reversals."
    ),
    "vwap": (
        "VWAP: Anchored VWAP (VND), the volume-weighted typical price "
        f"((H+L+C)/3) accumulated from a swing anchor in the trailing "
        f"{_VWAP_WINDOW} sessions. Each line gives both anchors: 'high-anchor' "
        f"runs from the highest close of the {_VWAP_WINDOW}-bar window (the cost "
        "basis of buyers at the top — overhead supply, usually resistance), "
        "'low-anchor' from the lowest close (the accumulation basis — usually "
        "support). Usage: Price above both is a strong tape, below both is weak, "
        "between them is the working range; reclaiming the high-anchor is the "
        "classic breakout confirmation. Tips: This is the same anchored VWAP the "
        f"platform charts and breakout strategies use. A ticker with under "
        f"{_VWAP_WINDOW} sessions of history returns N/A throughout — that is "
        "missing history, not a market holiday."
    ),
    "regime": (
        "REGIME: the platform's volatility-regime filter (GKYZ(21) volatility "
        "min-max normalized to [0,1], with hysteresis — the state flips to "
        "risk-on when the normalized value crosses above 0.8 and back to "
        "risk-off only when it crosses below 0.2, so it is sticky rather than "
        "flickering). Each line gives the ticker's own state first, then "
        f"{_REGIME_MARKET_SYMBOL}'s. IMPORTANT: in this platform's vocabulary "
        "'RISK-ON' means the HIGH-VOLATILITY state and 'RISK-OFF' the "
        "LOW-VOLATILITY state — it is a volatility label, NOT a directional or "
        "bullish/bearish call, so never read RISK-ON as 'buy'. Usage: it is a "
        "position-sizing and entry filter — the same one the platform's "
        "backtests gate on; risk-on argues for smaller size and wider stops on "
        "trend entries, risk-off for calmer conditions. Tips: the state is "
        "seeded risk-off at the start of the loaded window, so treat a long "
        "unbroken run of risk-off at the far end of the look-back as possibly "
        "un-warmed rather than meaningful; pair it with atr for the size of the "
        "move, since gkyz alone says nothing about direction."
    ),
    "obv": (
        "OBV: On-Balance Volume, a running total that adds the day's volume on "
        "an up close and subtracts it on a down close (shares, cumulative from "
        "the start of the loaded ~2-year window, so the level is arbitrary — "
        "only its slope and divergences carry meaning). Usage: Confirm a trend "
        "when OBV makes new highs/lows with price. Tips: OBV rising while price "
        "stalls signals accumulation; falling while price holds signals "
        "distribution."
    ),
}

# stockstats scales MFI 0-1; every reference (and the description above) uses
# 0-100, so rescale rather than teach the model a non-standard threshold.
_IND_SCALE: dict[str, float] = {"mfi": 100.0}

# Above this magnitude the 4-decimal rendering is noise, not precision — OBV runs
# to billions of shares. Prices and oscillators stay below it and are unaffected.
_LARGE_VALUE = 1e6

# ──────────────────────────────────────────────────────────────────────────
# Company news — curated research reports + wichart xbrain-news
# ──────────────────────────────────────────────────────────────────────────
# Research reports listed per call by ``_report_section``.
_MAX_REPORTS = 8
_MAX_SUMMARIES = 4  # cap the (slower) DeltaLake body lookups per call

# The same two caps for the xbrain-news company feed: headlines shown, and the
# length each item's summary is trimmed to.
_WICHART_NEWS_MAX = int(os.getenv("TRADINGAGENTS_COMPANY_NEWS_MAX", "12"))
_WICHART_NEWS_SUMMARY_CHARS = 700

# ──────────────────────────────────────────────────────────────────────────
# Global / macro news (get_global_news)
# ──────────────────────────────────────────────────────────────────────────
# Macro/market queries powering get_global_news. Override with
# TRADINGAGENTS_NEWS_QUERIES (comma-separated) — defaults blend Vietnam-market
# and global-macro angles.
_DEFAULT_NEWS_QUERIES = (
    "Vietnam stock market VN-Index news",
    "State Bank of Vietnam interest rate inflation",
    "Vietnam economy GDP export FDI outlook",
    "global markets Fed interest rates macro outlook",
)

# KB chunks pulled per macro query. Deliberately below the company-level default:
# several queries run per call and the macro backdrop wants breadth across topics,
# not depth on one strategy report.
_GLOBAL_KB_TOP_K = int(os.getenv("TRADINGAGENTS_GLOBAL_KB_TOP_K", "3"))

# Tier 2 of get_global_news: wichart's Vietnam macro stream — the same
# xbrain-news feed ``get_macro_indicators`` reads, but unfiltered by topic, so one
# call covers rates, FX, SBV operations, prices, growth and credit at once.
_GLOBAL_FEED_FETCH_LIMIT = int(os.getenv("TRADINGAGENTS_GLOBAL_FEED_LIMIT", "60"))
_GLOBAL_FEED_MAX_ITEMS = int(os.getenv("TRADINGAGENTS_GLOBAL_FEED_MAX_ITEMS", "10"))

# Which queries the feed can answer. It carries Vietnam only (``category_type``
# "Thế giới" returns nothing), so a query about the Fed, the dollar or China has
# to go to the web tier — matching on these markers is what keeps the domestic
# topics off the open web without silently dropping the global ones.
_VN_QUERY_MARKERS = (
    "vietnam",
    "viet nam",
    "việt",
    "vn-index",
    "vnindex",
    "vnd",
    "sbv",
    "hose",
    "hnx",
)

# ──────────────────────────────────────────────────────────────────────────
# Macro indicators (get_macro_indicators)
# ──────────────────────────────────────────────────────────────────────────
# The upstream tool is written against FRED, so the analyst asks for US-flavoured
# aliases ("cpi", "fed_funds_rate", "10y_treasury"). Map those onto the feed's own
# Vietnamese topic tags where an equivalent exists; anything unmapped is passed to
# the feed's free-text search, and a miss degrades to the general macro digest.
_MACRO_TAG_ALIASES: dict[str, str] = {
    # policy & market rates
    "fed_funds_rate": "Lãi suất",
    "policy_rate": "Lãi suất",
    "interest_rate": "Lãi suất",
    "rates": "Lãi suất",
    "rate": "Lãi suất",
    "interbank": "Lãi suất",
    "10y_treasury": "Lãi suất",
    "yield_curve": "Lãi suất",
    # central-bank operations / liquidity
    "monetary_policy": "Chính sách tiền tệ",
    "liquidity": "Chính sách tiền tệ",
    "omo": "Chính sách tiền tệ",
    "sbv": "Chính sách tiền tệ",
    "money_supply": "Chính sách tiền tệ",
    "m2": "Chính sách tiền tệ",
    # currency
    "fx": "Tỷ giá",
    "usd": "Tỷ giá",
    "usdvnd": "Tỷ giá",
    "exchange_rate": "Tỷ giá",
    "currency": "Tỷ giá",
    # prices
    "cpi": "Giá cả",
    "inflation": "Giá cả",
    "core_pce": "Giá cả",
    "prices": "Giá cả",
    # activity
    "real_gdp": "Tăng trưởng kinh tế",
    "gdp": "Tăng trưởng kinh tế",
    "growth": "Tăng trưởng kinh tế",
    "pmi": "Sản xuất",
    "manufacturing": "Sản xuất",
    "industrial_production": "Sản xuất",
    "fdi": "Đầu tư",
    "investment": "Đầu tư",
    "trade": "Giao dịch quốc tế",
    "exports": "Giao dịch quốc tế",
    "imports": "Giao dịch quốc tế",
    "trade_balance": "Giao dịch quốc tế",
    "retail_sales": "Tiêu dùng",
    "consumption": "Tiêu dùng",
    "credit": "Hệ thống ngân hàng",
    "banking": "Hệ thống ngân hàng",
    # labour has no counterpart in this feed — left unmapped on purpose so the
    # caller gets the "not covered" note instead of an unrelated tag's items.
}

# Trailing window when the caller does not supply look_back_days. The upstream
# FRED tool defaults to a year, but this is a news feed publishing several items a
# day: a month is already ~40 dated releases.
_MACRO_WINDOW_DAYS = int(os.getenv("TRADINGAGENTS_MACRO_WINDOW_DAYS", "90"))
_MACRO_MAX_ITEMS = int(os.getenv("TRADINGAGENTS_MACRO_MAX_ITEMS", "12"))
# Rows fetched per request before date filtering — the feed is not date-filterable
# server-side, so the window is applied here and needs headroom to work with.
_MACRO_FETCH_LIMIT = int(os.getenv("TRADINGAGENTS_MACRO_FETCH_LIMIT", "60"))
_MACRO_SUMMARY_CHARS = 700

# ──────────────────────────────────────────────────────────────────────────
# Fundamentals — ruatichsan statements + 24hmoney company index
# ──────────────────────────────────────────────────────────────────────────
# The API returns all three statements in one payload keyed by these short names.
_STATEMENTS: dict[str, tuple[str, str]] = {
    "cdkt": ("balance_sheet", "Balance sheet (Cân đối kế toán)"),
    "kqkd": ("income_statement", "Income statement (Kết quả kinh doanh)"),
    "lctt": ("cashflow", "Cash flow (Lưu chuyển tiền tệ)"),
}

# Periods shown per statement. The API returns 30+ quarters, which is far more
# than an analyst prompt should carry; the most recent five drive the narrative.
_STMT_PERIODS = int(os.getenv("TRADINGAGENTS_FUNDAMENTALS_PERIODS", "12"))

# (label, latest field, trailing-4-quarters field, decimals). The "4Q" variants
# are the same metric over the last four reported quarters, i.e. TTM.
_RATIO_ROWS: tuple[tuple[str, str, str | None, int], ...] = (
    ("P/E", "pe", "pe4Q", 2),
    ("P/B", "pb", "pb4Q", 2),
    ("EPS (VND/share)", "eps", "eps4Q", 0),
    ("EPS diluted (VND/share)", "eps_diluted", "eps_4q_diluted", 0),
    ("Book value (VND/share)", "book_value", "book_value4Q", 0),
    ("ROE (%)", "roe", "roe4Q", 2),
    ("ROA (%)", "roa", "roa4Q", 2),
    ("EV/EBITDA", "ev_per_ebitda", "ev_per_ebitda4Q", 2),
    ("EV/EBIT", "ev_per_ebit", "ev_per_ebit4Q", 2),
    ("Beta", "the_beta", "the_beta4Q", 2),
)

# ---------------------------------------------------------------------------
# Core OHLCV loader (shared by price, indicator, and snapshot tools)
# ---------------------------------------------------------------------------


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


def _calc_vwap(df: pd.DataFrame) -> pd.Series:
    """Anchored VWAP, both anchors, via the platform's own ``avwap``.

    Same implementation the charts and the breakout strategies use
    (``app.services.indicators.vwap``): find the highest (and separately the
    lowest) close in the trailing ``_VWAP_WINDOW`` bars, then volume-weight the
    typical price from that bar to today. Deliberately *not* a rolling window
    average — the anchor is what makes the line a cost basis.

    Both anchors ship in one value because they are read as a pair: overhead
    supply above, accumulation basis below. Values are pre-rendered strings so
    the caller's formatter passes them through; bars before the window fills stay
    NaN and render as "N/A".
    """
    from app.services.indicators.vwap import avwap

    close = df["Close"].to_numpy(dtype=float)
    high = df["High"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)
    volume = df["Volume"].to_numpy(dtype=float)

    kwargs = dict(close=close, high=high, low=low, volume=volume, window=_VWAP_WINDOW)
    from_high = avwap(is_highest=True, **kwargs)
    from_low = avwap(is_highest=False, **kwargs)

    rendered = [
        f"high-anchor {h:,.2f} · low-anchor {lo:,.2f}"
        if pd.notna(h) and pd.notna(lo)
        else float("nan")
        for h, lo in zip(from_high, from_low)
    ]
    return pd.Series(rendered, index=df.index, dtype=object)


def _calc_obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume via the platform's own ``obv_2d`` (one symbol column).

    Running Σ(±volume) signed by the close-to-close move, flat on an unchanged
    close. The total starts at 0 on the first loaded bar, so the *level* is
    relative to the loaded window — slope and divergence are what the analyst
    reads.
    """
    from app.services.indicators.common import obv_2d

    close = df["Close"].to_numpy(dtype=float).reshape(-1, 1)
    volume = df["Volume"].to_numpy(dtype=float).reshape(-1, 1)
    return pd.Series(obv_2d(close, volume)[:, 0], index=df.index)


def _gkyz_regime(df: pd.DataFrame) -> pd.DataFrame:
    """Normalized GKYZ volatility and its sticky risk-on/risk-off state.

    Thin wrapper over the platform's own regime signal
    (``app.services.indicators.regime_signals.gkyz_hysteresis``: GKYZ(21)
    min-max normalized to [0,1], flip to risk-on above 0.8, back to risk-off
    below 0.2) — the same filter the backtests gate entries on, so the analyst
    and the strategies read one definition.

    Note the platform's convention: ``risk_on=True`` means the *high-volatility*
    state, not "bullish". Both the value and the state are causal.
    """
    from app.services.indicators.gkyz_volatility import calculate_gkyz_volatility
    from app.services.indicators.regime_signals import GKYZ_WINDOW, gkyz_hysteresis

    ohlc = [df[col].to_numpy(dtype=float) for col in ("Open", "High", "Low", "Close")]
    gkyz = calculate_gkyz_volatility(*ohlc, window=GKYZ_WINDOW, normalize=True)
    risk_on = gkyz_hysteresis(*ohlc, index=df.index, window=GKYZ_WINDOW)
    return pd.DataFrame({"gkyz": gkyz, "risk_on": risk_on.to_numpy()}, index=df.index)


# Keyed by curr_date: one analyst run asks for the same as-of date across every
# ticker it looks at, so the index frame is fetched once per run.
_market_regime_cache: dict[str, pd.DataFrame | None] = {}


def _market_regime(curr_date: str) -> pd.DataFrame | None:
    """The index's regime frame, indexed by ``YYYY-MM-DD``; None if unavailable.

    VNINDEX is excluded from the watchlist but loadable by name (the backtest
    service does the same). A missing index must degrade the market leg to "n/a"
    rather than sink the whole indicator.
    """
    if curr_date not in _market_regime_cache:
        try:
            market = load_ohlcv(_REGIME_MARKET_SYMBOL, curr_date)
            regime = _gkyz_regime(market)
            regime.index = pd.to_datetime(market["Date"]).dt.strftime("%Y-%m-%d")
            _market_regime_cache[curr_date] = regime
        except Exception as exc:  # noqa: BLE001 — a data gap must not kill the tool
            logger.warning(
                "Market regime unavailable for %s as of %s: %s",
                _REGIME_MARKET_SYMBOL, curr_date, exc,
            )
            _market_regime_cache[curr_date] = None
    return _market_regime_cache[curr_date]


def _regime_label(risk_on: Any, gkyz: Any) -> str:
    state = "RISK-ON (high-vol)" if bool(risk_on) else "RISK-OFF (low-vol)"
    return f"{state} gkyz={gkyz:.2f}" if pd.notna(gkyz) else f"{state} gkyz=n/a"


def _calc_regime(df: pd.DataFrame) -> pd.Series:
    """The ticker's own volatility regime alongside the market's, per bar.

    Both legs on one line because that is how the strategies read them: a
    ``risk_regime`` for the name and a ``market_risk_regime`` for the tape.
    """
    own = _gkyz_regime(df)
    days = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
    market = _market_regime(days.iloc[-1]) if len(days) else None

    rendered = []
    for pos, day in enumerate(days):
        own_part = _regime_label(own["risk_on"].iat[pos], own["gkyz"].iat[pos])
        if market is not None and day in market.index:
            row = market.loc[day]
            market_part = _regime_label(row["risk_on"], row["gkyz"])
        else:
            market_part = "n/a"
        rendered.append(
            f"{own_part} · {_REGIME_MARKET_SYMBOL} {market_part}"
        )
    return pd.Series(rendered, index=df.index, dtype=object)


def _calc_kama(df: pd.DataFrame) -> pd.Series:
    """KAMA(10) via TA-Lib — the same call the chart's ``kama`` study makes.

    stockstats also ships a ``kama``, but on different defaults (fast 5 / slow 34)
    and without squaring the smoothing constant, so its line would not match the
    one the user sees on the chart.
    """
    import talib

    return pd.Series(
        talib.KAMA(df["Close"].to_numpy(dtype=float), timeperiod=_KAMA_PERIOD),
        index=df.index,
    )


# Indicators computed from the platform's own indicator library rather than
# stockstats — either because stockstats lacks them (VWAP, OBV) or because its
# definition disagrees with the one the charts draw (KAMA).
_CUSTOM_INDICATORS: dict[str, Any] = {
    "vwap": _calc_vwap,
    "obv": _calc_obv,
    "kama": _calc_kama,
    "regime": _calc_regime,
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

    if indicator in _CUSTOM_INDICATORS:
        series = _CUSTOM_INDICATORS[indicator](df)
    else:
        # wrap() mutates its argument in place, so hand it a copy and keep df
        # intact for the (capitalized) Date column — stockstats lowercases columns.
        work = df.copy()
        stock_df = wrap(work)
        series = stock_df[indicator]  # triggers stockstats calculation
    scale = _IND_SCALE.get(indicator)
    if scale:
        series = series * scale
    dates = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d").tolist()
    values = dict(zip(dates, list(series.values)))

    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = curr_dt - relativedelta(days=look_back_days)

    lines = []
    cursor = curr_dt
    while cursor >= before:
        key = cursor.strftime("%Y-%m-%d")
        value = values.get(key)
        if key not in values:
            rendered = "N/A: Not a trading day (weekend or holiday)"
        elif value is None or pd.isna(value):
            # The bar traded but the indicator has no value yet — it needs more
            # prior history than the frame holds (200 bars for vwap, 200 for
            # close_200_sma). Saying "not a trading day" here would be a lie the
            # analyst then repeats.
            rendered = "N/A: insufficient prior history for this indicator"
        elif isinstance(value, float):
            rendered = (
                f"{value:,.0f}" if abs(value) >= _LARGE_VALUE else f"{value:.4f}"
            )
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


def _wichart_company_news(sym: str, start_date: str, end_date: str) -> str | None:
    """Company headlines from wichart's xbrain-news feed, filtered by ticker.

    Uses the ``codetag`` firehose filter (see ``wichart_news_client``). Returns
    None on any failure or when no items fall in the window, so the caller can
    keep degrading toward the web-search fallback.
    """
    from app.services import wichart_news_client as news

    try:
        items = news.fetch_news(
            category_type=None,
            codetag=sym,
            limit=max(_WICHART_NEWS_MAX * 2, 20),
        )
    except Exception as exc:  # noqa: BLE001 — enrichment only, never fail the tool
        logger.warning("wichart company news unavailable for %s: %s", sym, exc)
        return None

    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)

    # publish_date is null for some item types (e.g. insider-trade registrations),
    # so fall back to the datapoint date, then the underlying report's ngaykn,
    # then created_at — otherwise those headlines drop out entirely.
    def _day(item: dict[str, Any]) -> str:
        meta = item.get("metadata")
        meta_day = iso_day(meta.get("ngaykn")) if isinstance(meta, dict) else ""
        return (
            iso_day(item.get("publish_date"))
            or iso_day(item.get("indicator_data_date"))
            or meta_day
            or iso_day(item.get("created_at"))
        )

    dated = [(d, i) for i in items if (d := _day(i))]
    in_range = [(d, i) for d, i in dated if start <= pd.to_datetime(d) <= end]

    note = ""
    if not in_range:
        # Items are sparse; fall back to the most recent ones before end_date.
        in_range = [(d, i) for d, i in dated if pd.to_datetime(d) <= end]
        if in_range:
            note = (
                " (No items fell strictly within the requested window; showing the "
                "most recent prior headlines for context.)"
            )

    if not in_range:
        return None

    in_range.sort(key=lambda pair: pair[0], reverse=True)
    shown = in_range[:_WICHART_NEWS_MAX]

    parts = [f"# Company news for {sym} ({start_date} to {end_date}){note}", ""]
    for day, item in shown:
        title = str(item.get("title") or "(untitled)").strip()
        parts.append(f"## {day} — {title}")

        # Analyst reports carry a rating + target price in metadata; surface it.
        meta = item.get("metadata")
        if isinstance(meta, dict):
            bits = []
            rec = str(meta.get("khuyennghi") or "").strip()
            if rec:
                bits.append(f"Rating: {rec}")
            target = meta.get("giamuctieu")
            if target:
                bits.append(f"Target: {fmt_ratio(target, 0)} VND")
            source = str(meta.get("nguon") or "").strip()
            if source:
                bits.append(f"By {source}")
            if bits:
                parts.append(" · ".join(bits))

        summary = str(item.get("ai_summary") or item.get("main_content_text") or "").strip()
        if summary:
            if len(summary) > _WICHART_NEWS_SUMMARY_CHARS:
                summary = summary[:_WICHART_NEWS_SUMMARY_CHARS].rstrip() + " …"
            parts.append(summary)
        parts.append("")
    return "\n".join(parts).strip()


def _company_news(ticker: str, start_date: str, end_date: str) -> str:
    """Company-level news, knowledge-base first.

    Tiered so we prefer our own curated research over the open web:
      1. **Knowledge base** — semantic search over embedded wichart research
         reports in Qdrant, filtered to this ticker (``kb_search``).
      2. If neither exists, the wichart xbrain-news company feed (``codetag``)
         provides live ticker-tagged headlines.
      3. If the KB has no match, the curated report *metadata* (ClickHouse) is a
         cheap secondary internal source.
      4. Only when we have no internal signal at all do we fall back to a live
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
        logger.info(
            "company_news[%s]: tier 1 knowledge base HIT (%d chunk(s))",
            sym, len(kb_hits),
        )
        body = kb_search.format_hits(f"Knowledge-base research for {sym}", kb_hits)
        note = (
            "Source: internal knowledge base (curated research reports). This is "
            "the primary company signal; base the assessment on it and do not "
            "fabricate headlines beyond what is shown."
        )
        return f"{body}\n\n{note}"
    logger.info("company_news[%s]: tier 1 knowledge base MISS", sym)

    # Tier 2: wichart xbrain-news company feed (ticker-tagged headlines).
    wichart_text = _wichart_company_news(sym, start_date, end_date)
    if wichart_text:
        logger.info("company_news[%s]: tier 2 wichart company feed HIT", sym)
        return (
            f"{wichart_text}\n\n"
            "Source: wichart xbrain-news company feed (no knowledge-base or "
            "research-report match). Base the assessment on these headlines; do "
            "not fabricate any beyond what is shown."
        )
    logger.info("company_news[%s]: tier 2 wichart company feed MISS", sym)

    # Tier 3: curated report metadata (reports that exist but aren't embedded yet).
    report_text = _report_section(sym, start_date, end_date)
    if report_text:
        logger.info("company_news[%s]: tier 3 curated report metadata HIT", sym)
        return (
            f"{report_text}\n\n"
            "Source: curated research reports (no knowledge-base match). Base the "
            "assessment on this evidence; do not fabricate headlines."
        )
    logger.info("company_news[%s]: tier 3 curated report metadata MISS", sym)

    # Tier 4: live web search fallback (no internal knowledge for this ticker).
    if ws.web_search_enabled():
        logger.info("company_news[%s]: tier 4 live web search fallback", sym)
        days = lookback_days(start_date, end_date)
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

    logger.warning(
        "company_news[%s]: all tiers exhausted (web search disabled/unavailable)",
        sym,
    )
    return (
        f"No company news available for {sym} (no knowledge-base match, no research "
        f"reports, and web search disabled/unavailable). Do not fabricate headlines."
    )


def _sector_enabled() -> bool:
    """Whether get_news appends the sector block (default yes)."""
    return os.getenv("TRADINGAGENTS_SECTOR_ANALYST", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def get_news(ticker: str, start_date: str, end_date: str) -> str:
    """Company news **plus sector context** for the News & Sentiment analysts.

    Upstream's analyst team has no sector role, so rather than adding a fifth
    graph node we widen this tool: the News Analyst — whose brief already covers
    "market conditions" — gets the ticker's industry, sector metrics and sector
    research alongside company news, and folds a sector view into its report.
    Set ``TRADINGAGENTS_SECTOR_ANALYST=0`` to serve company news only.
    """
    sym = ticker.upper()
    company = _company_news(sym, start_date, end_date)
    if not _sector_enabled():
        return company

    from . import sector_analyst

    sector = sector_analyst.build_sector_section(sym, end_date)
    return f"{company}\n\n---\n\n{sector}" if sector else company


# ---------------------------------------------------------------------------
# news_data : get_global_news, then the sentinels for the categories with no
# Vietnamese-market source configured
# ---------------------------------------------------------------------------


def _news_queries() -> list[str]:
    raw = os.getenv("TRADINGAGENTS_NEWS_QUERIES")
    if raw:
        queries = [q.strip() for q in raw.split(",") if q.strip()]
        if queries:
            return queries
    return list(_DEFAULT_NEWS_QUERIES)


def _kb_hit_key(hit: dict[str, Any]) -> tuple:
    """Identity of one KB chunk, so overlapping queries don't repeat it."""
    return (hit.get("pdf_url") or hit.get("title"), hit.get("page"))


def _is_vn_query(query: str) -> bool:
    text = str(query).lower()
    return any(marker in text for marker in _VN_QUERY_MARKERS)


def _vn_macro_stream_section(curr_date: str | None, look_back_days: int) -> str | None:
    """Recent Vietnamese macro releases from wichart's xbrain-news stream.

    Same feed and same rendering as ``get_macro_indicators``, minus the topic
    filter: one page of the Vietnam stream, trimmed to the window and grouped per
    indicator, so a series the feed republishes every session (the interbank
    overnight rate, the USD/VND fix, OMO volumes) collapses into one block plus a
    value history instead of a dozen paraphrases.

    Returns None when the feed is unreachable or has nothing in the window, so
    the caller can fall through to the web tier.
    """
    from app.services import wichart_news_client as news

    end = iso_day(curr_date) or datetime.now().strftime("%Y-%m-%d")
    start = (
        datetime.strptime(end, "%Y-%m-%d") - pd.Timedelta(days=look_back_days)
    ).strftime("%Y-%m-%d")

    try:
        items = news.fetch_news(
            category_type=news.MACRO_CATEGORY_TYPE, limit=_GLOBAL_FEED_FETCH_LIMIT
        )
    except Exception as exc:  # noqa: BLE001 — degrade to the web tier
        logger.warning("Vietnam macro stream unavailable: %s", exc)
        return None

    in_window = [item for item in items if _macro_in_window(item, start, end)]
    if not in_window:
        logger.info("No Vietnam macro releases between %s and %s", start, end)
        return None

    in_window.sort(key=lambda i: iso_day(i.get("publish_date")), reverse=True)
    series: dict[str, list[dict[str, Any]]] = {}
    for item in in_window:
        series.setdefault(_macro_series_key(item), []).append(item)
    shown = list(series.values())[:_GLOBAL_FEED_MAX_ITEMS]

    parts = [f"# Vietnam macro releases ({start} → {end})"]
    parts += [_macro_item_block(group[0], group[1:]) for group in shown]
    if len(series) > len(shown):
        parts.append(
            f"{len(series) - len(shown)} further indicator(s) in this window omitted."
        )
    return "\n\n".join(parts)


def get_global_news(curr_date=None, look_back_days=None, limit=None) -> str:
    """Macro/market news, internal sources first.

    Three tiers, each covering what the one above it could not:

      1. **Knowledge base** — semantic search over our embedded macro/strategy
         research, per query (``kb_search``).
      2. **wichart's Vietnam macro feed** — one digest of the dated releases in
         the window, answering the domestic topics the KB missed. Preferred over a
         web search for anything Vietnamese: the items are dated and carry the
         published datapoint (rate, fix, volume) behind the headline.
      3. **Live web search** — the global/US topics, which that feed does not
         carry at all, plus the domestic ones if it came back empty.

    A run therefore usually mixes sources, and the footer says which ones.
    """
    from . import kb_search, web_search as ws

    days = int(look_back_days) if look_back_days else 30
    per_query = int(limit) if limit else ws.DEFAULT_MAX_RESULTS
    # Spread the result budget across queries so one topic can't dominate.
    per_query = max(2, min(per_query, 5))
    web_ok = ws.web_search_enabled()

    sections: list[str] = []
    seen: set[tuple] = set()
    uncovered: list[str] = []
    used_kb = False
    used_feed = False
    used_web = False

    for query in _news_queries():
        # Tier 1: knowledge base. Unfiltered by symbol — macro topics are not
        # tied to one ticker. Returns [] when disabled/unreachable.
        hits = kb_search.search(query, top_k=_GLOBAL_KB_TOP_K)
        if hits:
            fresh = [h for h in hits if _kb_hit_key(h) not in seen]
            seen.update(_kb_hit_key(h) for h in fresh)
            if fresh:
                logger.info(
                    "global_news: tier 1 knowledge base HIT for %r (%d fresh chunk(s))",
                    query, len(fresh),
                )
                sections.append(kb_search.format_hits(query, fresh))
                sections.append("")
                used_kb = True
            # Hits that were all shown under an earlier query still count as
            # covered: don't spend a lower tier re-answering the same topic.
            continue
        uncovered.append(query)
    if uncovered:
        logger.info("global_news: tier 1 knowledge base left uncovered: %s", uncovered)

    # Tier 2: one Vietnam-feed digest for every domestic topic the KB left open.
    if any(_is_vn_query(query) for query in uncovered):
        feed = _vn_macro_stream_section(curr_date, days)
        if feed:
            logger.info("global_news: tier 2 Vietnam macro feed HIT")
            sections.append(feed)
            sections.append("")
            used_feed = True
        else:
            logger.info("global_news: tier 2 Vietnam macro feed MISS")

    # Tier 3: the web, for the topics neither tier above can serve.
    if web_ok:
        for query in uncovered:
            if used_feed and _is_vn_query(query):
                continue
            logger.info("global_news: tier 3 live web search for %r", query)
            sections.append(ws.search_and_format(query, max_results=per_query, days=days))
            sections.append("")
            used_web = True
    elif uncovered:
        logger.info("global_news: tier 3 web search disabled/unavailable")

    logger.info(
        "global_news: tiers used — kb=%s feed=%s web=%s",
        used_kb, used_feed, used_web,
    )
    if not sections:
        return (
            "GLOBAL_NEWS_UNAVAILABLE: the knowledge base returned no macro "
            "research, the Vietnamese macro feed had no releases in this window, "
            "and web search is disabled or unavailable. Base your assessment on "
            "company-level reports from get_news; do not fabricate macro headlines."
        )

    used = []
    if used_kb:
        used.append(
            "internal knowledge base (curated research reports — undated excerpts, "
            "so background rather than breaking news)"
        )
    if used_feed:
        used.append(
            "wichart's Vietnamese macro feed (dated releases, each with the "
            "published datapoint)"
        )
    if used_web:
        used.append("live web search (the topics the sources above do not cover)")

    header = f"# Global / macro & Vietnam-market news (as of {curr_date or 'now'})"
    footer = (
        f"Sources: {'; '.join(used)}. Synthesize the macro backdrop from these "
        f"results; cite concrete headlines with their dates and do not invent figures."
    )
    return "\n".join([header, "", *sections, footer])


def get_insider_transactions(ticker: str) -> str:
    return (
        f"INSIDER_DATA_UNAVAILABLE: No insider-transaction feed is configured for "
        f"Vietnamese equities ({str(ticker).upper()}). Do not fabricate filings."
    )


# ── Macro indicators (wichart xbrain-news) ──────────────────────────────────


def _macro_in_window(item: dict[str, Any], start: str, end: str) -> bool:
    day = iso_day(item.get("publish_date")) or iso_day(
        item.get("indicator_data_date")
    )
    if not day:
        return False
    return start <= day <= end


def _macro_series_key(item: dict[str, Any]) -> str:
    """Which indicator an item reports on.

    The feed republishes the same series daily (the overnight interbank rate, the
    USD/VND fix, OMO volumes), so one topic tag returns a dozen near-identical
    items. Grouping on the datapoint's own title collapses those into one block
    plus a value history, which buys breadth across indicators instead of a dozen
    paraphrases of the same release.
    """
    info = item.get("macro_info")
    if isinstance(info, dict) and info.get("title"):
        return str(info["title"]).strip()
    return str(item.get("title") or "").strip()[:40]


def _macro_value(item: dict[str, Any]) -> str:
    info = item.get("macro_info")
    if not isinstance(info, dict) or info.get("value") is None:
        return ""
    try:
        return f"{float(info['value']):,.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(info["value"])


def _macro_item_block(item: dict[str, Any], older: list[dict[str, Any]]) -> str:
    """Render one feed item: dated headline, datapoint, summary, link.

    ``older`` are superseded releases of the same series; they collapse into a
    one-line value history so the trend survives without repeating their prose.
    """
    day = iso_day(item.get("publish_date")) or "undated"
    title = str(item.get("title") or "(untitled)").strip()
    lines = [f"### {day} — {title}"]

    info = item.get("macro_info")
    value = _macro_value(item)
    if value and isinstance(info, dict):
        label = str(info.get("title") or "").strip()
        unit = str(info.get("unit") or "").strip()
        as_of = iso_day(info.get("data_date"))
        datapoint = " ".join(part for part in (f"**{value}**", unit) if part)
        prefix = f"{label}: " if label else ""
        suffix = f" (as of {as_of})" if as_of else ""
        lines.append(f"{prefix}{datapoint}{suffix}")

    history = [
        f"{_macro_value(o)} ({iso_day(o.get('publish_date'))[5:]})"
        for o in older
        if _macro_value(o)
    ]
    if history:
        lines.append("Earlier in the window: " + " · ".join(history))

    # ai_summary is the feed's own write-up of the release and is what we want:
    # present on every macro item, and byte-identical to main_content_text
    # wherever both exist (98/100 sampled), so there is no fallback to keep.
    summary = str(item.get("ai_summary") or "").strip()
    if summary:
        # A guard, not a routine trim: sampled summaries top out around 550 chars.
        if len(summary) > _MACRO_SUMMARY_CHARS:
            summary = summary[:_MACRO_SUMMARY_CHARS].rstrip() + " …"
        lines.append(summary)

    return "\n\n".join(lines)


def get_macro_indicators(
    indicator: str | None = None,
    curr_date: str | None = None,
    look_back_days: int | None = None,
) -> str:
    """Vietnamese macro releases for one indicator topic, from the wichart feed.

    The feed is Vietnam-only, so a request for a US series (a FRED ID, "core_pce",
    "10y_treasury") cannot be served on its own terms. Rather than a bare
    sentinel, those fall through to the general VN macro digest with an explicit
    note that the requested series is not covered — the VN backdrop is still the
    useful context, and the note is what stops the model inventing a US figure.
    """
    from app.services import wichart_news_client as news

    topic = str(indicator or "").strip()
    key = topic.lower().replace(" ", "_").replace("-", "_")
    tag = _MACRO_TAG_ALIASES.get(key)

    end = iso_day(curr_date) or datetime.now().strftime("%Y-%m-%d")
    days = int(look_back_days) if look_back_days else _MACRO_WINDOW_DAYS
    start = (datetime.strptime(end, "%Y-%m-%d") - pd.Timedelta(days=days)).strftime(
        "%Y-%m-%d"
    )

    def _fetch(**filters: Any) -> list[dict[str, Any]]:
        try:
            items = news.fetch_news(limit=_MACRO_FETCH_LIMIT, **filters)
        except Exception as exc:  # noqa: BLE001 — a data gap must not kill the graph
            logger.warning("Macro feed unavailable (%s): %s", filters, exc)
            raise
        return [i for i in items if _macro_in_window(i, start, end)]

    try:
        # Tier 1: the mapped topic tag, else the caller's own words as free text.
        if tag:
            logger.info(
                "macro_indicators[%s]: tier 1 tag_level_1=%r", topic or "<none>", tag
            )
            items = _fetch(tag_level_1=tag)
        elif topic:
            logger.info("macro_indicators[%s]: tier 1 free-text search", topic)
            items = _fetch(search=topic)
        else:
            logger.info("macro_indicators: no topic requested — skipping to digest")
            items = []
        # Tier 2: nothing for this topic (or none asked) → the general digest.
        scope = f"topic '{topic}'" if topic else "recent releases"
        matched = bool(items)
        if matched:
            logger.info(
                "macro_indicators[%s]: tier 1 HIT (%d item(s) in window)",
                topic or "<none>", len(items),
            )
        else:
            logger.info(
                "macro_indicators[%s]: tier 1 MISS — tier 2 general digest",
                topic or "<none>",
            )
            items = _fetch()
    except Exception as exc:  # noqa: BLE001
        return (
            f"MACRO_DATA_UNAVAILABLE: could not load the Vietnamese macro feed "
            f"({exc}). Proceed without it; do not fabricate macro figures."
        )

    if not items:
        return (
            f"MACRO_DATA_UNAVAILABLE: no Vietnamese macro releases published "
            f"between {start} and {end}. Do not fabricate macro figures."
        )

    items.sort(key=lambda i: iso_day(i.get("publish_date")), reverse=True)

    # One block per indicator, newest release first; repeats become a value history.
    series: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        series.setdefault(_macro_series_key(item), []).append(item)
    shown = list(series.values())[:_MACRO_MAX_ITEMS]

    heading = f"# Vietnam macro — {scope} ({start} → {end})"
    blocks = [_macro_item_block(group[0], group[1:]) for group in shown]

    # The substitution warning leads, so the model reads it before the data it
    # qualifies; provenance and truncation trail.
    lead = []
    if topic and not matched:
        lead.append(
            f"NOT COVERED: the feed has no Vietnamese release matching "
            f"'{topic}' in this window — it carries Vietnam macro only, with no "
            f"US/global series (no FRED equivalent). What follows is the general "
            f"Vietnamese macro backdrop instead; do not present it as '{topic}'."
        )

    trailer = []
    if len(series) > len(shown):
        trailer.append(
            f"{len(series) - len(shown)} further indicator(s) in this window omitted."
        )
    trailer.append(
        "Values are as published by wichart (Data Wi); cite the dates shown and do "
        "not extrapolate figures beyond them."
    )

    return "\n\n".join([heading, *lead, *blocks, *trailer])


def get_prediction_markets(*args, **kwargs) -> str:
    return (
        "PREDICTION_MARKETS_UNAVAILABLE: No prediction-market vendor is configured "
        "for this deployment. Proceed without event probabilities."
    )


# ── Fundamentals (ruatichsan financial statements) ──────────────────────────
# One payload serves all four tools, so memoize per (ticker, period) for the
# process — otherwise a single analyst turn refetches the same data four times.
_statement_cache: dict[tuple[str, str], Any] = {}


def _resolve_freq(freq: str | None) -> str:
    """Map the framework's annual/quarterly wording onto the API's path segment."""
    value = str(freq or "quarterly").strip().lower()
    return "annual" if value.startswith(("annual", "year", "yearly")) else "quarter"


def _load_statements(ticker: str, freq: str | None) -> Any:
    from app.services import ruatichsan_client as rts

    key = (ticker.upper(), _resolve_freq(freq))
    if key not in _statement_cache:
        _statement_cache[key] = rts.fetch_financial_statements(key[0], key[1])
    return _statement_cache[key]


def _statement_table(
    payload: Any,
    key: str,
    title: str,
    periods: int,
    max_rows: int | None = None,
) -> str:
    """Render one statement as a markdown table, newest period last.

    Rows are ``[title, _, _, *values]`` with the values aligned to the tail of
    ``fiscalDates``; rows that are zero in every shown period are dropped, since
    the API pads the chart of accounts with line items this company never uses.
    """
    dates = payload.get("fiscalDates") or []
    rows = payload.get(key) or []
    if not dates or not rows:
        return f"## {title}\nNo data returned."

    shown_dates = dates[-periods:]
    header = "| Line item (bn VND) | " + " | ".join(shown_dates) + " |"
    sep = "|---" * (len(shown_dates) + 1) + "|"
    lines = [f"## {title}", "", header, sep]

    kept = 0
    for row in rows:
        values = row[-len(dates):][-periods:]
        if all(v in (0, None) for v in values):
            continue
        lines.append(f"| {row[0]} | " + " | ".join(fmt_billion(v) for v in values) + " |")
        kept += 1
        if max_rows and kept >= max_rows:
            lines.append(f"| … ({len(rows) - kept}+ further line items omitted) | " + " | " * len(shown_dates))
            break
    return "\n".join(lines)


def _statement_tool(ticker: str, freq: str | None, key: str) -> str:
    """Shared body for the three per-statement tools."""
    sym = str(ticker).upper()
    _, title = _STATEMENTS[key]
    try:
        payload = _load_statements(sym, freq)
    except Exception as exc:  # noqa: BLE001 — a data gap must not kill the graph
        logger.warning("Financial statements unavailable for %s: %s", sym, exc)
        return (
            f"FUNDAMENTALS_UNAVAILABLE: could not load financial statements for "
            f"{sym} ({exc}). Do not fabricate figures."
        )
    body = _statement_table(payload, key, title, _STMT_PERIODS)
    return (
        f"# {sym} — {title}\n\n{body}\n\n"
        f"Values in billions of VND ({_resolve_freq(freq)} periods, oldest → newest). "
        f"Source: {payload.get('dataSource', 'ruatichsan')}."
    )


def get_balance_sheet(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    return _statement_tool(ticker, freq, "cdkt")


def get_income_statement(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    return _statement_tool(ticker, freq, "kqkd")


def get_cashflow(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    return _statement_tool(ticker, freq, "lctt")


# ── Fundamentals overview (24hmoney company index) ──────────────────────────
# Valuation/profitability ratios, sourced separately from the statements above:
# the statement API carries raw line items only, so P/E, P/B, ROE, EV multiples
# and the ownership structure come from 24hmoney's company-index endpoint.


def get_fundamentals(ticker: str, curr_date: str | None = None) -> str:
    """Valuation, profitability and ownership snapshot — the analyst's overview.

    Deliberately *not* a statement dump: the three per-statement tools cover the
    line items, so this call answers "how is this company priced and how
    profitable is it, versus its sector" instead.
    """
    sym = str(ticker).upper()
    try:
        from app.services import money24h_client

        data = money24h_client.fetch_company_index(sym)
    except Exception as exc:  # noqa: BLE001 — a data gap must not kill the graph
        logger.warning("Fundamentals unavailable for %s: %s", sym, exc)
        return (
            f"FUNDAMENTALS_UNAVAILABLE: could not load the fundamentals snapshot "
            f"for {sym} ({exc}). Do not fabricate figures."
        )

    lines = [f"# {sym} — fundamentals snapshot", ""]

    group = data.get("group_name")
    if group:
        peers = data.get("group_count")
        suffix = f" ({fmt_count(peers)} listed peers)" if peers else ""
        lines.append(f"Peer group: {group}{suffix}")
    chain = data.get("group_industry_full") or data.get("group_industry") or []
    # Broad → narrow ICB levels, minus the repeats: several sectors carry the
    # same label at every level ("Ngân hàng > Ngân hàng > Ngân hàng").
    names: list[str] = []
    for item in chain:
        name = item.get("icb_name")
        if name and (not names or names[-1] != str(name)):
            names.append(str(name))
    if names:
        lines.append(f"ICB industry: {' > '.join(names)}")
    if data.get("year"):
        lines.append(f"Ratios reference fiscal year: {data['year']}")
    lines.append("")

    lines += ["| Metric | Latest | Trailing 4Q |", "|---|---|---|"]
    for label, latest_key, ttm_key, digits in _RATIO_ROWS:
        latest = fmt_ratio(data.get(latest_key), digits)
        ttm = fmt_ratio(data.get(ttm_key), digits) if ttm_key else "-"
        if latest == "-" and ttm == "-":
            continue
        # Diluted EPS is only worth a row when it actually differs from basic.
        if latest_key == "eps_diluted" and (
            latest == fmt_ratio(data.get("eps"), digits)
            and ttm == fmt_ratio(data.get("eps4Q"), digits)
        ):
            continue
        lines.append(f"| {label} | {latest} | {ttm} |")
    lines.append("")

    market_cap = data.get("market_cap")
    if market_cap:
        lines.append(f"Market cap: {fmt_billion(market_cap)} bn VND")
    low, high = data.get("min_52w"), data.get("max_52w")
    if low and high:
        lines.append(
            f"52-week range: {fmt_ratio(low, 2)} – {fmt_ratio(high, 2)} "
            f"thousand VND per share"
        )
    if data.get("avg_trading_vol"):
        lines.append(
            f"Average trading volume: {fmt_count(data['avg_trading_vol'])} shares"
        )
    if data.get("listed_share_vol"):
        lines.append(f"Listed shares: {fmt_count(data['listed_share_vol'])}")
    if data.get("free_float"):
        rate = data.get("free_float_rate")
        pct = f" ({float(rate) * 100:,.1f}% of listed)" if rate else ""
        lines.append(f"Free float: {fmt_count(data['free_float'])} shares{pct}")
    if data.get("foreign_current_room") is not None:
        pct = data.get("foreign_current_room_percent")
        share = f" ({pct:,.2f}% of the cap)" if isinstance(pct, (int, float)) else ""
        lines.append(
            f"Foreign ownership room left: "
            f"{fmt_count(data['foreign_current_room'])} of "
            f"{fmt_count(data.get('foreign_total_room'))} shares{share}"
        )
    if data.get("audit_firm_name"):
        big4 = " — Big 4" if data.get("audit_is_big4") else ""
        year = data.get("audit_firm_year")
        lines.append(
            f"Auditor: {data['audit_firm_name']}{big4}"
            + (f" (FY{year})" if year else "")
        )

    lines += [
        "",
        "Ratios are as most recently published; '-' means the metric is not "
        "reported for this sector. Call get_balance_sheet / get_income_statement "
        "/ get_cashflow for the underlying line items (freq='annual' for yearly). "
        "Source: 24hmoney company index.",
    ]
    return "\n".join(lines)


# Method name -> VN implementation. Consumed by runner.register_vn_vendor().
VN_VENDOR_METHODS: dict[str, callable] = {
    "get_stock_data": get_stock_data,
    "get_indicators": get_indicators,
    "get_news": get_news,
    "get_global_news": get_global_news,
    "get_insider_transactions": get_insider_transactions,
    "get_macro_indicators": get_macro_indicators,
    "get_prediction_markets": get_prediction_markets,
    "get_fundamentals": get_fundamentals,
    "get_balance_sheet": get_balance_sheet,
    "get_income_statement": get_income_statement,
    "get_cashflow": get_cashflow,
}
