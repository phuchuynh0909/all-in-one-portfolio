"""Validation for hand-entered reports (POST /report).

Offline: only the request schema is exercised, no MySQL involved.
"""
import pytest
from pydantic import ValidationError

from app.schemas.report import ReportCreate


def test_normalizes_ticker_and_trims_text():
    payload = ReportCreate(
        tenbaocao="  VCB Q3 2025 update  ",
        url="  https://example.com/vcb.pdf ",
        mack=" vcb ",
        nguon=" SSI ",
        rsnganh="  ",
    )
    assert payload.tenbaocao == "VCB Q3 2025 update"
    assert payload.url == "https://example.com/vcb.pdf"
    assert payload.mack == "VCB"
    assert payload.nguon == "SSI"
    # Blank optionals become None rather than empty strings in the feed row.
    assert payload.rsnganh is None


def test_symbol_optional_and_source_defaults():
    payload = ReportCreate(tenbaocao="Market outlook 2026", url="http://example.com/a.pdf")
    assert payload.mack is None
    assert payload.nguon == "manual"
    assert payload.ngaykn is None  # service fills in "now"
    assert payload.id is None  # store allocates from the manual id band


def test_accepts_explicit_id():
    payload = ReportCreate(id=123456, tenbaocao="A report", url="https://example.com/a.pdf")
    assert payload.id == 123456


@pytest.mark.parametrize("bad_id", [0, -1])
def test_rejects_non_positive_id(bad_id):
    with pytest.raises(ValidationError):
        ReportCreate(id=bad_id, tenbaocao="A report", url="https://example.com/a.pdf")


@pytest.mark.parametrize("url", ["example.com/a.pdf", "ftp://example.com/a.pdf", ""])
def test_rejects_non_http_url(url):
    with pytest.raises(ValidationError):
        ReportCreate(tenbaocao="A report", url=url)


@pytest.mark.parametrize("title", ["", "   "])
def test_rejects_blank_title(title):
    with pytest.raises(ValidationError):
        ReportCreate(tenbaocao=title, url="https://example.com/a.pdf")
