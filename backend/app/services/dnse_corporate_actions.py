"""The DNSE corporate-action source: parsing and fetching.

DNSE reports corporate actions with the *amount inside a Vietnamese title
string* — there is no structured amount or ratio field. This module turns that
prose into numbers and refuses to guess: anything it cannot read confidently
comes back as ``None`` so the caller can store it for human review rather than
silently corrupting a cost basis.

Only three of the six observed event names move a position; meetings and
shareholder votes are ignored outright.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# DNSE ``name`` -> our action_type. Anything absent here is not price-affecting.
ACTION_NAMES: dict[str, str] = {
    "Trả cổ tức bằng tiền mặt": "cash",
    "Trả cổ tức bằng cổ phiếu": "stock",
    "Thưởng cổ phiếu": "stock",
}

# "... bằng tiền 800 đồng/CP"
_CASH_RE = re.compile(r"bằng\s+tiền\s*([\d.,]+)\s*đồng\s*/\s*CP", re.IGNORECASE)
# "... tỷ lệ 100:8", "tỷ lệ 100:10.5", "tỷ lệ 01:03"
_RATIO_RE = re.compile(r"tỷ\s*lệ\s*(\d+(?:[.,]\d+)?)\s*:\s*(\d+(?:[.,]\d+)?)")

# A number written with thousands groupings: 1.500 / 1,500 / 1.234.567
_GROUPED_RE = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")
# A plain number, optionally with a decimal fraction: 800 / 12.5
_PLAIN_RE = re.compile(r"^\d+(?:[.,]\d{1,2})?$")


@dataclass(frozen=True)
class ParsedAction:
    """What a title yielded. Exactly one of the two numbers is set."""

    action_type: str
    amount_per_share: Decimal | None
    ratio: Decimal | None


def classify(name: str) -> str | None:
    """``"cash"``, ``"stock"``, or ``None`` for an event we ignore."""
    return ACTION_NAMES.get((name or "").strip())


def _cash_amount(raw: str) -> Decimal | None:
    """Read a VND per-share amount.

    Vietnamese writes thousands as ``1.500``, which a naive decimal parse would
    turn into 1.5 — a thousand-fold error on a money figure. Grouped forms are
    therefore detected and stripped; only an ungrouped number keeps its
    fraction. Anything else is refused.
    """
    if _GROUPED_RE.match(raw):
        return Decimal(re.sub(r"[.,]", "", raw))
    if _PLAIN_RE.match(raw):
        try:
            return Decimal(raw.replace(",", "."))
        except InvalidOperation:
            return None
    return None


def _ratio(base_raw: str, new_raw: str) -> Decimal | None:
    """New shares per held share, from ``tỷ lệ base:new``.

    Here ``.``/``,`` is a decimal point (``100:10.5``), not a grouping — ratios
    are small by nature. Leading zeros (``01:03``) are harmless to Decimal.
    """
    try:
        base = Decimal(base_raw.replace(",", "."))
        new = Decimal(new_raw.replace(",", "."))
    except InvalidOperation:
        return None
    if base <= 0 or new <= 0:
        return None
    return new / base


def parse_action(name: str, title: str) -> ParsedAction | None:
    """Turn a DNSE ``(name, title)`` pair into numbers, or ``None``.

    ``None`` means "store it as unparsed and show a human" — never "assume
    zero".
    """
    action_type = classify(name)
    if action_type is None:
        return None

    text = title or ""
    if action_type == "cash":
        m = _CASH_RE.search(text)
        if not m:
            return None
        amount = _cash_amount(m.group(1))
        if amount is None or amount <= 0:
            return None
        return ParsedAction("cash", amount, None)

    m = _RATIO_RE.search(text)
    if not m:
        return None
    ratio = _ratio(m.group(1), m.group(2))
    if ratio is None:
        return None
    return ParsedAction("stock", None, ratio)
