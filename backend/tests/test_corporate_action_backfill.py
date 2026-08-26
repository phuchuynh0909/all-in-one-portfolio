"""Golden test: the eight real lots, end to end, against the live DNSE feed.

Numbers come from the spec's impact table. This runs inside the rolled-back
``db`` fixture, so it proves the outcome without changing anything.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text

from app.services import corporate_action_service as cas
from app.services import portfolio_service
from tests.conftest import requires_mysql

pytestmark = [requires_mysql, pytest.mark.slow]

EXPECTED = {
    27: (Decimal("11990.000000"), Decimal("14.054545")),
    29: (Decimal("10560.000000"), Decimal("13.000000")),
    33: (Decimal("79488.000000"), Decimal("18.891667")),
    4:  (Decimal("47611.000000"), Decimal("20.662494")),
    25: (Decimal("34020.000000"), Decimal("17.824074")),
    32: (Decimal("20520.000000"), Decimal("20.370370")),
    9:  (Decimal("107000.000000"), Decimal("13.233645")),
    1:  (Decimal("36594.000000"), Decimal("12.626168")),
}
# Thousands of VND, the unit every money column in positions/transactions uses.
# 307,044 price units == 307,044,000 VND of gross cash dividends.
EXPECTED_GROSS_CASH = Decimal("307044")
EXPECTED_SHARES_ADDED = Decimal("35523")


def test_backfilling_every_pending_event_reproduces_the_spec_numbers(db):
    invested_before = db.execute(
        text("SELECT SUM(quantity * purchase_price) FROM positions")
    ).scalar()

    cas.sync_all(db)

    pending = cas.list_actions(db, status="pending")
    # strictly ex-date order: later events must see earlier share counts
    assert [a.ex_date for a in pending] == sorted(a.ex_date for a in pending)

    shares = Decimal(0)
    cash = Decimal(0)
    for action in pending:
        # Applying one event settles its whole ex-date group, so a sibling may
        # already be applied by the time the loop reaches it (PAN 2026-05-29).
        db.refresh(action)
        if action.status != "pending":
            continue
        result = cas.apply_action(db, action.id)
        shares += result.total_shares_added
        cash += result.total_cash_gross

    assert cash == EXPECTED_GROSS_CASH
    assert shares == EXPECTED_SHARES_ADDED

    for position_id, (qty, price) in EXPECTED.items():
        position = portfolio_service.get_position(db, position_id)
        assert position is not None, f"lot {position_id} vanished"
        assert position.quantity == qty, f"lot {position_id} quantity"
        assert position.purchase_price == price, f"lot {position_id} price"

    invested_after = db.execute(
        text("SELECT SUM(quantity * purchase_price) FROM positions")
    ).scalar()
    # Cost preservation, within price quantization across 11 stock events.
    assert abs(Decimal(str(invested_after)) - Decimal(str(invested_before))) < Decimal("1")
