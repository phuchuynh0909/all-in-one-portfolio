"""Settlement engine: the arithmetic that must not be wrong.

Every number here was computed against the live DNSE feed and the eight real
lots; see the spec's impact table.
"""
from datetime import date
from decimal import Decimal

from app.services.corporate_action_engine import (
    Event,
    Lot,
    adjusted_price,
    settle,
    shares_added,
)


def lot(qty="1000", price="20", bought="2025-01-01", pid=1):
    return Lot(pid, Decimal(qty), Decimal(price), date.fromisoformat(bought))


def cash(ex, amount, cid=1):
    return Event(cid, "cash", date.fromisoformat(ex), Decimal(amount), None)


def stock(ex, ratio, cid=1):
    return Event(cid, "stock", date.fromisoformat(ex), None, Decimal(ratio))


def test_shares_added_truncates_never_rounds_up():
    # 40,820 x 0.08 = 3,265.6 -> 3,265. Round-half-even would give 3,266.
    assert shares_added(Decimal("40820"), Decimal("0.08")) == Decimal("3265")
    assert shares_added(Decimal("10900"), Decimal("0.1")) == Decimal("1090")
    assert shares_added(Decimal("999"), Decimal("0.999")) == Decimal("998")


def test_adjusted_price_preserves_total_cost():
    price = adjusted_price(Decimal("24.10"), Decimal("40820"), Decimal("44085"))
    assert price == Decimal("22.315119")


def test_cash_dividend_leaves_the_lot_untouched():
    [s] = settle(lot(qty="1000", price="20"), [cash("2025-06-01", "800")])

    assert s.action_type == "cash"
    assert s.cash_amount == Decimal("800000")
    assert s.qty_before == s.qty_after == Decimal("1000")
    assert s.price_before == s.price_after == Decimal("20")
    assert s.shares_added == Decimal("0")


def test_stock_dividend_grows_quantity_and_dilutes_basis():
    [s] = settle(lot(qty="10900", price="15.46"), [stock("2026-06-17", "0.1")])

    assert s.qty_after == Decimal("11990")
    assert s.price_after == Decimal("14.054545")
    assert s.shares_added == Decimal("1090")
    assert s.cash_amount is None


def test_lot_bought_after_ex_date_gets_nothing():
    late = lot(qty="1000", price="20", bought="2026-07-01")
    assert settle(late, [stock("2026-06-17", "0.1"), cash("2026-06-17", "800")]) == []


def test_lot_bought_on_the_ex_date_is_eligible():
    same = lot(qty="1000", price="20", bought="2026-06-17")
    assert len(settle(same, [stock("2026-06-17", "0.1")])) == 1


def test_events_sharing_an_ex_date_settle_off_one_opening_quantity():
    """PAN 2026-05-29: a bonus and a cash dividend on the same ex-date.

    Cash must be computed on the pre-bonus 66,240 shares. Settling it after the
    bonus would pay on 79,488 and overstate income by 39,744,000 VND.
    """
    events = [stock("2026-05-29", "0.2", cid=10), cash("2026-05-29", "3000", cid=11)]
    results = {s.corporate_action_id: s for s in settle(lot(qty="66240", price="22.67"), events)}

    assert results[11].cash_amount == Decimal("198720000")
    assert results[10].qty_after == Decimal("79488")
    assert results[10].price_after == Decimal("18.891667")


def test_cash_settlement_preserves_opening_quantity_in_mixed_groups():
    """Regression: cash records must report the ex-date's opening quantity, not diluted.

    In a same-ex-date group, a stock bonus changes the lot but the cash dividend
    must report the pre-bonus quantity. A consumer gating on `shares_added > 0`
    relies on this contract to avoid reverting the bonus (PAN: stock cid 10 sets
    qty to 79,488, then cash cid 11 must *not* revert it to 66,240 by reporting
    the post-dilution qty).
    """
    events = [stock("2026-05-29", "0.2", cid=10), cash("2026-05-29", "3000", cid=11)]
    results = {s.corporate_action_id: s for s in settle(lot(qty="66240", price="22.67"), events)}

    # Stock event should have moved the quantity
    assert results[10].qty_after == Decimal("79488")
    # But cash event must report the opening quantity, not the diluted one
    assert results[11].qty_before == Decimal("66240")
    assert results[11].qty_after == Decimal("66240")
    assert results[11].price_before == Decimal("22.67")
    assert results[11].price_after == Decimal("22.67")
    assert results[11].shares_added == Decimal("0")
    # Explicit contract: stock and cash report different quantities
    assert results[10].qty_after != results[11].qty_after


def test_same_ex_date_order_of_events_does_not_matter():
    a = [stock("2026-05-29", "0.2", cid=10), cash("2026-05-29", "3000", cid=11)]
    b = [cash("2026-05-29", "3000", cid=11), stock("2026-05-29", "0.2", cid=10)]
    as_map = lambda evs: {s.corporate_action_id: s.cash_amount for s in settle(lot(qty="66240", price="22.67"), evs)}
    assert as_map(a) == as_map(b)


def test_later_events_compound_off_earlier_ones():
    """VCG lot 4: 2025-06-11 then 2026-07-14, both 100:8, plus two cash events."""
    events = [
        cash("2025-05-27", "800", cid=1),
        stock("2025-06-11", "0.08", cid=2),
        cash("2026-07-14", "800", cid=3),
        stock("2026-07-14", "0.08", cid=4),
    ]
    out = {s.corporate_action_id: s for s in settle(lot(qty="40820", price="24.10", bought="2025-04-08"), events)}

    assert out[1].cash_amount == Decimal("32656000")
    assert out[2].qty_after == Decimal("44085")
    # the 2026 cash pays on the post-2025-bonus count, pre-2026-bonus
    assert out[3].cash_amount == Decimal("35268000")
    assert out[4].qty_after == Decimal("47611")
    assert out[4].price_after == Decimal("20.662494")


def test_total_cost_is_preserved_within_price_quantization():
    """Exact equality would fail: the price is quantized to 6 decimals.

    VCG lot 4 drifts 0.0018 on a cost of 983,762 across its two stock events.
    The honest bound is one ulp of the price times the share count.
    """
    start = lot(qty="40820", price="24.10", bought="2025-04-08")
    cost_before = start.quantity * start.purchase_price
    events = [stock("2025-06-11", "0.08", cid=2), stock("2026-07-14", "0.08", cid=4)]
    results = settle(start, events)
    last = results[-1]
    cost_after = last.qty_after * last.price_after

    tolerance = Decimal("0.000001") * last.qty_after * len(results)
    assert abs(cost_after - cost_before) <= tolerance


def test_events_are_applied_in_ex_date_order_regardless_of_input_order():
    events = [stock("2026-07-14", "0.08", cid=4), stock("2025-06-11", "0.08", cid=2)]
    results = settle(lot(qty="40820", price="24.10", bought="2025-04-08"), events)
    assert [s.corporate_action_id for s in results] == [2, 4]
    assert results[-1].qty_after == Decimal("47611")


def test_stock_event_adding_zero_shares_is_skipped():
    """A tiny lot with a tiny ratio floors to zero: nothing to record."""
    assert settle(lot(qty="5", price="20"), [stock("2026-01-01", "0.08")]) == []


def test_an_unknown_action_type_is_rejected_not_treated_as_stock():
    """Rights issues are a named future type; a silent fallthrough is a bug.

    The stock branch adds shares for free. A rights issue the holder actually
    pays for would land there and quietly halve the cost basis, so the engine
    must refuse rather than guess.
    """
    import pytest

    rights = Event(7, "rights", date(2026, 6, 1), Decimal("10000"), Decimal("0.5"))
    with pytest.raises(ValueError, match="rights"):
        settle(lot(qty="1000", price="20"), [rights])
