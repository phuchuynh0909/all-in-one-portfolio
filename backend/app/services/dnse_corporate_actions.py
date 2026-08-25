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
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import requests

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


# Complete per-symbol history. Past events only — the sibling calendar endpoint
# holds upcoming ones but ignores every filter parameter, so it is unused here.
# Because this endpoint is complete, a missed poll self-heals on the next run.
HISTORY_URL = "https://api-bo.dnse.com.vn/senses-api/corporate-actions/history"

_TIMEOUT = 30


@dataclass(frozen=True)
class RawEvent:
    """One DNSE row, dates decoded, text untouched."""

    symbol: str
    event_id: int
    name: str
    title: str
    ex_date: date | None
    record_date: date | None
    pay_date: date | None
    url: str | None


def _as_date(value: Any) -> date | None:
    """``2026-07-14T00:00:00+07:00`` -> date. Empty string -> None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def fetch_history(
    symbol: str,
    *,
    session: Optional[Any] = None,
    page_size: int = 100,
) -> list[RawEvent]:
    """Every past corporate action for ``symbol``, oldest ``ex_date`` first.

    ``session`` takes anything with a ``requests``-style ``get`` so tests need
    no network. Paging stops on an empty page as well as on ``total``, because
    trusting ``total`` alone would loop forever if it ever overreported.
    """
    http = session or requests
    collected: list[RawEvent] = []
    page = 1

    while True:
        response = http.get(
            HISTORY_URL,
            params={"symbol": symbol, "pageSize": page_size, "page": page},
            headers={"Accept": "application/json"},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("corporateActions") or []
        if not rows:
            break

        for row in rows:
            collected.append(
                RawEvent(
                    symbol=(row.get("symbol") or symbol).strip().upper(),
                    event_id=int(row["eventId"]),
                    name=(row.get("name") or "").strip(),
                    title=(row.get("title") or "").strip(),
                    ex_date=_as_date(row.get("exRightsDate")),
                    record_date=_as_date(row.get("recordDate")),
                    pay_date=_as_date(row.get("actionDate")),
                    url=row.get("url") or None,
                )
            )

        if len(collected) >= int(payload.get("total") or 0):
            break
        page += 1

    collected.sort(key=lambda e: (e.ex_date or date.min, e.event_id))
    return collected
