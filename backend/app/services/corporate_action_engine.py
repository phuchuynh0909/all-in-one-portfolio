"""Pure settlement: what a set of corporate actions does to one lot.

Kept free of the database and the ORM so the arithmetic — which is where the
real risk lives — can be tested exhaustively without a server. The service
layer turns these results into rows.

Four rules, each learned the hard way:

* A lot is eligible only if it existed on the ex-date.
* Every event sharing an ex-date settles against the *same* opening quantity.
  Paying a cash dividend on a share count a same-day bonus just inflated
  overstates income (PAN 2026-05-29: by 39,744,000 VND).
* Added shares truncate; Vietnam does not trade fractions.
* The diluted price is derived from the *floored* share count, not from
  ``1+ratio``, so the rounding residue cannot leak into total cost.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_FLOOR, Decimal

PRICE_SCALE = Decimal("0.000001")  # DECIMAL(15,6)


@dataclass(frozen=True)
class Lot:
    position_id: int
    quantity: Decimal
    purchase_price: Decimal
    purchase_date: date


@dataclass(frozen=True)
class Event:
    corporate_action_id: int
    action_type: str  # "cash" | "stock"
    ex_date: date
    amount_per_share: Decimal | None
    ratio: Decimal | None


@dataclass(frozen=True)
class Settlement:
    corporate_action_id: int
    position_id: int
    action_type: str
    qty_before: Decimal
    qty_after: Decimal
    price_before: Decimal
    price_after: Decimal
    shares_added: Decimal
    cash_amount: Decimal | None


def shares_added(quantity: Decimal, ratio: Decimal) -> Decimal:
    """Whole new shares from a ratio, truncated.

    ``40820 x 0.08 = 3265.6`` yields 3,265. Rounding up would invent a share.
    """
    return (quantity * ratio).to_integral_value(rounding=ROUND_FLOOR)


def adjusted_price(price: Decimal, qty_before: Decimal, qty_after: Decimal) -> Decimal:
    """Per-share cost after dilution, holding total cost constant."""
    if qty_after <= 0:
        return price
    return (price * qty_before / qty_after).quantize(PRICE_SCALE)


def settle(lot: Lot, events: list[Event]) -> list[Settlement]:
    """Settlements for one lot, in ex-date order.

    Events the lot is not entitled to, and stock events too small to add a
    whole share, produce nothing.
    """
    eligible = [e for e in events if e.ex_date >= lot.purchase_date]
    eligible.sort(key=lambda e: (e.ex_date, e.corporate_action_id))

    quantity, price = lot.quantity, lot.purchase_price
    out: list[Settlement] = []

    for _, group in itertools.groupby(eligible, key=lambda e: e.ex_date):
        # One opening quantity for the whole ex-date, then one quantity change.
        opening_qty, opening_price = quantity, price
        added_total = Decimal(0)
        staged: list[tuple[Event, Decimal, Decimal | None]] = []

        for event in group:
            if event.action_type == "cash":
                if event.amount_per_share is None:
                    continue
                staged.append((event, Decimal(0), event.amount_per_share * opening_qty))
            elif event.action_type == "stock":
                if event.ratio is None:
                    continue
                added = shares_added(opening_qty, event.ratio)
                if added <= 0:
                    continue
                added_total += added
                staged.append((event, added, None))
            else:
                # An unrecognised type must not fall through to the stock
                # branch. Rights issues are a named future type: treated as a
                # bonus issue they would add shares for free that the holder
                # actually has to pay for, quietly halving the cost basis.
                raise ValueError(
                    f"Unsupported corporate action type {event.action_type!r} "
                    f"on event {event.corporate_action_id}; expected "
                    "'cash' or 'stock'"
                )

        if not staged:
            continue

        quantity = opening_qty + added_total
        if added_total:
            price = adjusted_price(opening_price, opening_qty, quantity)

        for event, added, cash_amount in staged:
            out.append(
                Settlement(
                    corporate_action_id=event.corporate_action_id,
                    position_id=lot.position_id,
                    action_type=event.action_type,
                    qty_before=opening_qty,
                    qty_after=quantity if added else opening_qty,
                    price_before=opening_price,
                    price_after=price if added else opening_price,
                    shares_added=added,
                    cash_amount=cash_amount,
                )
            )

    return out
