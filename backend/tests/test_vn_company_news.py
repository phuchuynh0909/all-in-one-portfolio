"""Tests for the wichart xbrain-news company-news tier in vn_data.

All upstream calls are mocked — no live wichart/DNSE requests. Fixtures mirror
the real ``xbrain-news?codetag=KBC`` payload shape (analyst reports with a
metadata rating/target, plus insider-trade items whose ``publish_date`` is null
and whose date lives in ``metadata.ngaykn`` / ``created_at``).
"""
from __future__ import annotations

from unittest import mock

from app.services.tradingagents import kb_search, vn_data


# A trimmed but shape-accurate slice of the live codetag=KBC response.
_KBC_ITEMS = [
    {
        "title": "KBC: Cập nhật",
        "publish_date": "2026-08-12T00:00:00.000Z",
        "ai_summary": "ACBS duy trì khuyến nghị khả quan đối với cổ phiếu KBC.",
        "main_content_text": None,
        "metadata": {
            "nguon": "ACBS",
            "ngaykn": "2026-08-13T00:00:00.000Z",
            "giamuctieu": 32300,
            "khuyennghi": "MUA",
        },
    },
    {
        # Insider-trade item: publish_date is null; date must come from metadata.
        "title": "CTCP ... đã đăng ký mua vào 10 triệu cổ phiếu KBC",
        "publish_date": None,
        "indicator_data_date": None,
        "ai_summary": "Đăng ký mua vào 10 triệu cổ phiếu KBC.",
        "main_content_text": "Đăng ký mua vào 10 triệu cổ phiếu KBC.",
        "created_at": "2026-08-10T15:53:47.000Z",
        "metadata": {"table": "gd_noibo", "status": "đã thực hiện"},
    },
]


def test_wichart_company_news_renders_reports_and_metadata():
    with mock.patch(
        "app.services.wichart_news_client.fetch_news", return_value=_KBC_ITEMS
    ) as fn:
        out = vn_data._wichart_company_news("KBC", "2026-08-01", "2026-08-31")

    # codetag firehose, not the macro stream.
    assert fn.call_args.kwargs["codetag"] == "KBC"
    assert fn.call_args.kwargs["category_type"] is None

    assert out is not None
    # Report headline + rating/target/source line from metadata.
    assert "KBC: Cập nhật" in out
    assert "Rating: MUA" in out
    assert "Target: 32,300 VND" in out
    assert "By ACBS" in out
    # Insider item survives via created_at even with null publish_date.
    assert "đã đăng ký mua vào 10 triệu" in out
    assert "2026-08-10" in out


def test_wichart_company_news_out_of_window_falls_back_to_recent():
    with mock.patch(
        "app.services.wichart_news_client.fetch_news", return_value=_KBC_ITEMS
    ):
        # Window entirely after the items — should still show recent prior ones.
        out = vn_data._wichart_company_news("KBC", "2026-12-01", "2026-12-31")

    assert out is not None
    assert "most recent prior headlines" in out
    assert "KBC: Cập nhật" in out


def test_wichart_company_news_empty_returns_none():
    with mock.patch("app.services.wichart_news_client.fetch_news", return_value=[]):
        assert vn_data._wichart_company_news("KBC", "2026-08-01", "2026-08-31") is None


def test_wichart_company_news_upstream_error_returns_none():
    with mock.patch(
        "app.services.wichart_news_client.fetch_news", side_effect=RuntimeError("boom")
    ):
        assert vn_data._wichart_company_news("KBC", "2026-08-01", "2026-08-31") is None


def test_company_news_falls_through_kb_and_reports_to_wichart():
    with mock.patch.object(kb_search, "search", return_value=[]), mock.patch.object(
        vn_data, "_report_section", return_value=None
    ), mock.patch(
        "app.services.wichart_news_client.fetch_news", return_value=_KBC_ITEMS
    ):
        out = vn_data._company_news("KBC", "2026-08-01", "2026-08-31")

    assert "wichart xbrain-news company feed" in out
    assert "KBC: Cập nhật" in out

def test_real_call():
    out = vn_data._wichart_company_news("KBC", "2026-08-01", "2026-08-31")
    print(out)
    assert out is not None