"""Parser tests over real DNSE titles collected from held tickers' history.

The amount exists only inside Vietnamese prose, so a misparse silently
corrupts cost basis. Every case here came from the live feed.
"""
from decimal import Decimal

import pytest

from app.services.dnse_corporate_actions import ParsedAction, classify, parse_action

CASH = "Trả cổ tức bằng tiền mặt"
STOCK = "Trả cổ tức bằng cổ phiếu"
BONUS = "Thưởng cổ phiếu"


@pytest.mark.parametrize(
    "name,expected",
    [
        (CASH, "cash"),
        (STOCK, "stock"),
        (BONUS, "stock"),
        ("Họp ĐHCĐ bất thường", None),
        ("Họp ĐHCĐ thường niên", None),
        ("Lấy ý kiến CĐ bằng văn bản", None),
        ("Something we have never seen", None),
    ],
)
def test_classify(name, expected):
    assert classify(name) == expected


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Trả cổ tức năm 2025 bằng tiền 800 đồng/CP", Decimal("800")),
        ("Trả cổ tức năm 2025 bằng tiền 500 đồng/CP", Decimal("500")),
        ("Trả cổ tức năm 2026 bằng tiền 3000 đồng/CP", Decimal("3000")),
        ("Trả cổ tức năm 2025 bằng tiền 1500 đồng/CP", Decimal("1500")),
        # thousands separators must not be read as decimals: 1.500 is 1500, not 1.5
        ("Trả cổ tức năm 2025 bằng tiền 1.500 đồng/CP", Decimal("1500")),
        ("Trả cổ tức năm 2025 bằng tiền 1,500 đồng/CP", Decimal("1500")),
        # a genuine sub-unit amount stays a decimal
        ("Trả cổ tức năm 2025 bằng tiền 12.5 đồng/CP", Decimal("12.5")),
    ],
)
def test_parse_cash(title, expected):
    got = parse_action(CASH, title)
    assert got == ParsedAction("cash", expected, None)


@pytest.mark.parametrize(
    "name,title,expected",
    [
        (STOCK, "Trả cổ tức năm 2025 bằng cổ phiếu tỷ lệ 100:8", Decimal("0.08")),
        (STOCK, "Trả cổ tức năm 2025 bằng cổ phiếu tỷ lệ 100:10", Decimal("0.1")),
        (STOCK, "Trả cổ tức năm 2025 bằng cổ phiếu tỷ lệ 1000:75", Decimal("0.075")),
        (STOCK, "Trả cổ tức năm 2026 bằng cổ phiếu tỷ lệ 100:6", Decimal("0.06")),
        # multi-year titles appear verbatim in the feed
        (STOCK, "Trả cổ tức năm 2024, 2025 bằng cổ phiếu tỷ lệ 100:17", Decimal("0.17")),
        (BONUS, "Thưởng cổ phiếu tỷ lệ 100:20", Decimal("0.2")),
        (BONUS, "Thưởng cổ phiếu tỷ lệ 100:7", Decimal("0.07")),
        # decimal ratio
        (BONUS, "Thưởng cổ phiếu tỷ lệ 100:10.5", Decimal("0.105")),
        (BONUS, "Thưởng cổ phiếu tỷ lệ 2:1", Decimal("0.5")),
        # zero-padded, TRC's real title
        (BONUS, "Thưởng cổ phiếu tỷ lệ 01:03", Decimal("3")),
    ],
)
def test_parse_stock(name, title, expected):
    got = parse_action(name, title)
    assert got.action_type == "stock"
    assert got.amount_per_share is None
    assert got.ratio == expected


@pytest.mark.parametrize(
    "name,title",
    [
        (CASH, "Trả cổ tức năm 2025 bằng cổ phiếu tỷ lệ 100:8"),  # cash name, no amount
        (CASH, "Trả cổ tức năm 2025"),
        (STOCK, "Trả cổ tức năm 2025 bằng cổ phiếu"),  # no ratio
        (BONUS, "Thưởng cổ phiếu tỷ lệ 100:0"),  # zero ratio is meaningless
        (BONUS, "Thưởng cổ phiếu tỷ lệ 0:10"),  # zero base would divide by zero
        (CASH, "Trả cổ tức bằng tiền 1.23.45 đồng/CP"),  # malformed number
    ],
)
def test_unparseable_returns_none(name, title):
    assert parse_action(name, title) is None


def test_ignored_name_returns_none_even_with_parseable_body():
    assert parse_action("Họp ĐHCĐ bất thường", "Thưởng cổ phiếu tỷ lệ 100:8") is None
