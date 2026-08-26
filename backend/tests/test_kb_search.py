"""KB symbol filter: empty string is a real match (market-wide chunks)."""
from __future__ import annotations

from app.services.tradingagents import kb_search, vn_data


def test_empty_symbol_is_a_qdrant_match_not_unfiltered():
    assert kb_search._symbol_filter(None) is None
    assert kb_search._symbol_filter([]) is None

    filt = kb_search._symbol_filter([""])
    assert filt is not None
    cond = filt.must[0]
    assert cond.key == "symbol"
    assert list(cond.match.any) == [""]


def test_ticker_filter_still_uppercases():
    filt = kb_search._symbol_filter(["hpg"])
    assert list(filt.must[0].match.any) == ["HPG"]


def test_global_news_kb_search_filters_empty_symbol(monkeypatch):
    calls: list[dict] = []

    def fake_search(query, symbols=None, top_k=None, **_kwargs):
        calls.append({"query": query, "symbols": symbols, "top_k": top_k})
        return []

    monkeypatch.setenv("TRADINGAGENTS_NEWS_QUERIES", "vietnam market")
    monkeypatch.setattr(
        "app.services.tradingagents.kb_search.search", fake_search
    )
    monkeypatch.setattr(
        "app.services.tradingagents.web_search.web_search_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.services.tradingagents.vn_data._vn_macro_stream_section",
        lambda *_a, **_k: None,
    )

    vn_data.get_global_news("2026-08-01", 30, 5)

    assert calls
    assert all(c["symbols"] == [""] for c in calls)
    assert all(c["top_k"] == vn_data._GLOBAL_KB_TOP_K for c in calls)


# ---------------------------------------------------------------------------
# search_knowledge_base: the tool the news analyst calls with its own question
# ---------------------------------------------------------------------------


def _hit(**over):
    base = {
        "score": 0.71,
        "symbol": "HPG",
        "title": "Báo cáo cập nhật HPG",
        "page": 2,
        "pdf_url": "https://example.test/hpg.pdf",
        "text": "Sản lượng thép xây dựng phục hồi.",
    }
    base.update(over)
    return base


def test_search_knowledge_base_filters_to_the_given_ticker(monkeypatch):
    calls: list[dict] = []

    def fake_search(query, symbols=None, **_kw):
        calls.append({"query": query, "symbols": symbols})
        return [_hit()]

    monkeypatch.setattr(kb_search, "search", fake_search)

    out = vn_data.search_knowledge_base("triển vọng lợi nhuận thép", ticker="hpg")

    assert calls == [{"query": "triển vọng lợi nhuận thép", "symbols": ["HPG"]}]
    assert "Báo cáo cập nhật HPG" in out
    assert "Sản lượng thép xây dựng phục hồi." in out


def test_search_knowledge_base_is_unfiltered_without_a_ticker(monkeypatch):
    calls: list[dict] = []

    def fake_search(query, symbols=None, **_kw):
        calls.append({"query": query, "symbols": symbols})
        return [_hit(symbol="")]

    monkeypatch.setattr(kb_search, "search", fake_search)

    vn_data.search_knowledge_base("chính sách tiền tệ của NHNN")

    assert calls[0]["symbols"] is None


def test_search_knowledge_base_miss_reports_the_miss_without_a_web_fallback(monkeypatch):
    monkeypatch.setattr(kb_search, "search", lambda *_a, **_kw: [])

    def explode(*_a, **_kw):  # pragma: no cover — asserts it is never reached
        raise AssertionError("search_knowledge_base must not fall back to the web")

    monkeypatch.setattr(
        "app.services.tradingagents.web_search.search_and_format", explode
    )

    out = vn_data.search_knowledge_base("giá vé máy bay nội địa", ticker="HPG")

    assert "NO_KB_MATCH" in out
    assert "get_global_news" in out


def test_search_knowledge_base_error_returns_the_unavailable_sentinel(monkeypatch):
    def boom(*_a, **_kw):
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(kb_search, "search", boom)

    out = vn_data.search_knowledge_base("triển vọng ngành thép")

    assert out.startswith("KB_UNAVAILABLE")


def test_register_vn_vendor_repoints_the_frameworks_knowledge_base(monkeypatch):
    """The KB tool bypasses route_to_vendor; registration is what wires it up."""
    from app.services.tradingagents import runner

    monkeypatch.setattr(runner, "_registered", False)
    runner.register_vn_vendor()

    from tradingagents.dataflows import knowledge_base

    assert knowledge_base.search_kb is vn_data.search_knowledge_base
