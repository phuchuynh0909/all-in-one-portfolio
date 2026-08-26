"""Every vn_data tool must degrade to a sentinel rather than raise.

The graph reaches these through ``interface.route_to_vendor``, which re-raises a
vendor's exception once no other vendor can serve the call — and only
``macro_data``/``prediction_markets`` are in its OPTIONAL_CATEGORIES. "portfolio"
is the sole vendor registered for the rest, so anything that escapes a tool ends
the run. These tests pin the two guarantees that prevent that: no tool raises,
and none returns an empty answer that an analyst would read as "no constraint".

``NoMarketDataError`` is the deliberate exception — the router renders it as
NO_DATA_AVAILABLE, naming the symbol and the reason, which beats any sentinel
written here.
"""
from __future__ import annotations

from unittest import mock

import pandas as pd
import pytest

from app.services.tradingagents import vn_data


# (method name, positional args) for every entry in VN_VENDOR_METHODS.
_CALLS = [
    ("get_stock_data", ("ZZZ", "2026-01-01", "2026-08-01")),
    ("get_indicators", ("ZZZ", "close_50_sma", "2026-08-01")),
    ("get_news", ("ZZZ", "2026-01-01", "2026-08-01")),
    ("get_global_news", ("2026-08-01", 30, 5)),
    ("get_insider_transactions", ("ZZZ",)),
    ("get_macro_indicators", ("cpi", "2026-08-01", 30)),
    ("get_prediction_markets", ()),
    ("get_fundamentals", ("ZZZ",)),
    ("get_balance_sheet", ("ZZZ",)),
    ("get_income_statement", ("ZZZ",)),
    ("get_cashflow", ("ZZZ",)),
]

# Arguments an LLM can plausibly get wrong: unparseable dates, an indicator that
# does not exist, a frequency the API never accepts, None for a ticker.
_BAD_ARGS = [
    ("get_stock_data", ("ZZZ", "Aug 1 2026", "yesterday")),
    ("get_indicators", ("ZZZ", "not_an_indicator", "2026-08-01")),
    ("get_macro_indicators", (None, "garbage", "many")),
    ("get_news", (None, None, None)),
    ("get_balance_sheet", ("ZZZ", "weekly")),
]


def _boom(*_args, **_kwargs):
    raise RuntimeError("simulated source outage")


@pytest.fixture
def dead_sources(monkeypatch):
    """Break every external source the tools reach."""
    for target in (
        "app.services.stock_service._load_delta_stocks",
        "app.services.tradingagents.kb_search.search",
        "app.services.tradingagents.kb_search.format_hits",
        "app.services.tradingagents.web_search.search_and_format",
        "app.services.tradingagents.sector_analyst.build_sector_section",
        "app.services.wichart_news_client.fetch_news",
        "app.services.money24h_client.fetch_company_index",
        "app.services.ruatichsan_client.fetch_financial_statements",
        "app.services.report_service._query_raw_reports",
    ):
        monkeypatch.setattr(target, _boom)
    monkeypatch.setattr(
        "app.services.tradingagents.web_search.web_search_enabled", lambda: True
    )
    # The statements memo would otherwise serve a cached payload.
    vn_data._statement_cache.clear()
    yield
    vn_data._statement_cache.clear()


def test_every_vendor_method_is_covered():
    """A new tool must be added to _CALLS, not silently skipped."""
    assert {name for name, _ in _CALLS} == set(vn_data.VN_VENDOR_METHODS)


@pytest.mark.parametrize("name,args", _CALLS)
def test_tool_returns_sentinel_when_every_source_fails(dead_sources, name, args):
    out = vn_data.VN_VENDOR_METHODS[name](*args)
    assert isinstance(out, str) and out.strip()
    assert "UNAVAILABLE" in out
    # The model must be told not to fill the gap itself.
    assert "fabricate" in out.lower()


@pytest.mark.parametrize("name,args", _BAD_ARGS)
def test_bad_arguments_do_not_raise(name, args):
    out = vn_data.VN_VENDOR_METHODS[name](*args)
    assert isinstance(out, str) and out.strip()


def test_blank_return_becomes_a_sentinel():
    @vn_data.failsafe("TEST_UNAVAILABLE", "the thing")
    def empty() -> str:
        return "   "

    assert empty().startswith("TEST_UNAVAILABLE")


def test_no_market_data_error_still_reaches_the_router(monkeypatch):
    """It is a verdict, not a failure: the router turns it into NO_DATA_AVAILABLE."""
    no_data = vn_data._passthrough_types()
    if not no_data:
        pytest.skip("TradingAgents framework not installed")
    monkeypatch.setattr(
        "app.services.stock_service._load_delta_stocks", lambda **_k: pd.DataFrame()
    )
    with pytest.raises(no_data):
        vn_data.get_stock_data("ZZZ", "2026-01-01", "2026-08-01")


def test_no_data_verdict_survives_a_missing_framework(monkeypatch):
    """Without TradingAgents installed there is no router, so the tool renders it.

    The failure mode this pins: reporting the framework's absence as
    "ModuleNotFoundError" inside a data-unavailable sentinel, which tells the
    analyst the symbol has no data when the deployment is simply misconfigured.
    """
    monkeypatch.setattr(vn_data, "_PASSTHROUGH", ())
    monkeypatch.setattr(
        "app.services.stock_service._load_delta_stocks", lambda **_k: pd.DataFrame()
    )
    assert vn_data._no_market_data_cls() is vn_data._NoMarketData
    out = vn_data.get_stock_data("ZZZ", "2026-01-01", "2026-08-01")
    assert out.startswith("NO_DATA_AVAILABLE")
    assert "ModuleNotFoundError" not in out


def test_news_halves_degrade_independently(monkeypatch):
    """A broken sector view must not cost the company news, nor the reverse."""
    monkeypatch.setattr(vn_data, "_company_news", lambda *_a: "COMPANY BODY")
    monkeypatch.setattr(
        "app.services.tradingagents.sector_analyst.build_sector_section", _boom
    )
    assert vn_data.get_news("ZZZ", "2026-01-01", "2026-08-01") == "COMPANY BODY"

    monkeypatch.setattr(vn_data, "_company_news", _boom)
    monkeypatch.setattr(
        "app.services.tradingagents.sector_analyst.build_sector_section",
        lambda *_a: "SECTOR BODY",
    )
    out = vn_data.get_news("ZZZ", "2026-01-01", "2026-08-01")
    assert "SECTOR BODY" in out and "NEWS_UNAVAILABLE" in out


def test_one_bad_statement_row_does_not_drop_the_table():
    payload = {
        "fiscalDates": ["2026-03-31", "2026-06-30"],
        "kqkd": [
            ["Doanh thu", 1_000_000_000, 2_000_000_000],
            None,  # malformed line item
            ["Lợi nhuận", 3_000_000_000, 4_000_000_000],
        ],
    }
    table = vn_data._statement_table(payload, "kqkd", "Income statement", periods=2)
    assert "Doanh thu" in table and "Lợi nhuận" in table
