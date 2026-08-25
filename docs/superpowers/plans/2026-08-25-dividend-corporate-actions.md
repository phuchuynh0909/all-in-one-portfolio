# Dividend & Corporate Action Handling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record Vietnamese cash and stock dividends against per-lot positions so `quantity`, `purchase_price` and both P/L figures stay correct.

**Architecture:** Two new tables archive DNSE corporate-action events and log what was applied to which lot. A pure parser turns Vietnamese title prose into an amount or ratio; a pure settlement engine turns lots plus events into mutations; a thin DB service persists them in one transaction. Cash dividends are income and never touch cost basis; stock dividends mutate the lot preserving total cost. Events are captured automatically but applied only on explicit request, because amounts come from free text.

**Tech Stack:** Python 3.11, SQLAlchemy 2.x ORM, MySQL 8.0 (`my_portfolio`), Alembic, FastAPI, Pydantic v2, `requests`, pytest (`asyncio_mode=auto`).

**Spec:** `docs/superpowers/specs/2026-08-25-dividend-corporate-actions-design.md`

## Global Constraints

- All commands run from `backend/`. The app runs in Docker (`docker compose exec -T backend …`); tests may be run on the host from `backend/`.
- Database is MySQL `my_portfolio` at `192.168.1.3:3306`. Current Alembic head is `c4d8e1f60b93`. Never point Alembic at SQLite.
- `alembic check` must report "No new upgrade operations detected" at the end of any task that touches models or migrations.
- Money and share columns are `DECIMAL`; never use `float` for them. Prices are quantized to `DECIMAL(15,6)`.
- Prices are in thousands of VND (a price of `24.10` means 24,100 VND). Cash `amount_per_share` from DNSE is in **VND** (`800` means 800 VND) — do not rescale it; store as given.
- Added shares are `floor(quantity × ratio)` — truncation, never round-half-even.
- Vietnamese text must round-trip; the database is `utf8mb4_unicode_ci`. Source files are UTF-8.
- Any DNSE title that does not parse is stored `status='unparsed'` and applied to nothing. Never guess an amount.
- Only these three `name` values affect a position: `Trả cổ tức bằng tiền mặt` (cash), `Trả cổ tức bằng cổ phiếu` (stock), `Thưởng cổ phiếu` (stock). All other names are skipped entirely.
- Non-goals — do not implement: rights issues (`quyền mua`), dividends on closed positions, splits/consolidations/delistings, the forward calendar endpoint, automatic application without review.

## File Structure

**Create:**
- `backend/app/services/dnse_corporate_actions.py` — the DNSE source: title parser (pure) plus paged HTTP fetch. No database.
- `backend/app/services/corporate_action_engine.py` — pure settlement: lots + events → mutations. No database, no network.
- `backend/app/services/corporate_action_service.py` — DB-facing: sync, list, apply, unapply, ignore, manual entry.
- `backend/app/db/models/corporate_action.py` — `CorporateAction`, `CorporateActionApplication`.
- `backend/app/schemas/corporate_action.py` — Pydantic request/response models.
- `backend/app/api/v1/routes/corporate_actions.py` — routes under prefix `/portfolio`.
- `backend/alembic/versions/d5a91c3e7b20_corporate_actions.py` — tables + `transactions.transaction_type` ENUM widening.
- `backend/tasks/sync_corporate_actions.py` — daily poll entry point.
- `backend/tests/conftest.py` — shared rolled-back MySQL `db` fixture.
- `backend/tests/test_corporate_action_parser.py`, `test_corporate_action_engine.py`, `test_corporate_action_service_mysql.py`.

**Modify:**
- `backend/app/schemas/portfolio.py` — relax `TransactionBase`; add two `PortfolioSummary` fields.
- `backend/app/services/portfolio_service.py` — dividend income aggregate; wire into summary.
- `backend/app/api/v1/__init__.py` (or wherever routers are registered) — include the new router.
- `backend/tests/test_portfolio_service_mysql.py` — drop the local `db` fixture in favour of `conftest.py`.

Corporate-action logic deliberately stays out of `portfolio_service.py`, which is already 345 lines; it gains only the dividend aggregate.

---

### Task 1: Vietnamese title parser

**Files:**
- Create: `backend/app/services/dnse_corporate_actions.py`
- Test: `backend/tests/test_corporate_action_parser.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ACTION_NAMES: dict[str, str]` mapping the three relevant DNSE `name` values to `"cash"` / `"stock"`.
  - `@dataclass(frozen=True) ParsedAction(action_type: str, amount_per_share: Decimal | None, ratio: Decimal | None)`
  - `classify(name: str) -> str | None` — `"cash"`, `"stock"`, or `None` for names we ignore.
  - `parse_action(name: str, title: str) -> ParsedAction | None` — `None` means unparseable.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_corporate_action_parser.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_corporate_action_parser.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.dnse_corporate_actions'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/services/dnse_corporate_actions.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_corporate_action_parser.py -q`
Expected: PASS, 31 tests

- [ ] **Step 5: Commit**

```bash
git add app/services/dnse_corporate_actions.py tests/test_corporate_action_parser.py
git commit -m "feat: parse Vietnamese DNSE corporate-action titles"
```

---

### Task 2: DNSE history fetch

**Files:**
- Modify: `backend/app/services/dnse_corporate_actions.py` (append)
- Test: `backend/tests/test_corporate_action_parser.py` (append)

**Interfaces:**
- Consumes: nothing from Task 1 beyond living in the same module.
- Produces:
  - `HISTORY_URL: str`
  - `@dataclass(frozen=True) RawEvent(symbol, event_id, name, title, ex_date, record_date, pay_date, url)` — dates are `date | None`.
  - `fetch_history(symbol: str, *, session=None, page_size: int = 100) -> list[RawEvent]` — all pages, oldest-first by `ex_date`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_corporate_action_parser.py`:

```python
from datetime import date
from unittest.mock import Mock

from app.services.dnse_corporate_actions import RawEvent, fetch_history


def _page(rows, total):
    r = Mock()
    r.raise_for_status = Mock()
    r.json = Mock(return_value={"corporateActions": rows, "total": total,
                                "page": 1, "pageSize": 100})
    return r


def _row(sym="VCG", eid=1, name=CASH, ex="2026-07-14T00:00:00+07:00", pay=""):
    return {"symbol": sym, "eventId": eid, "name": name,
            "title": "Trả cổ tức năm 2025 bằng tiền 800 đồng/CP",
            "exRightsDate": ex, "recordDate": "2026-07-15T00:00:00+07:00",
            "actionDate": pay, "url": "https://example.test/x"}


def test_fetch_history_parses_rows_and_dates():
    session = Mock()
    session.get = Mock(return_value=_page([_row()], 1))

    events = fetch_history("VCG", session=session)

    assert events == [RawEvent(
        symbol="VCG", event_id=1, name=CASH,
        title="Trả cổ tức năm 2025 bằng tiền 800 đồng/CP",
        ex_date=date(2026, 7, 14), record_date=date(2026, 7, 15),
        pay_date=None, url="https://example.test/x",
    )]


def test_fetch_history_follows_pages_until_total_reached():
    session = Mock()
    session.get = Mock(side_effect=[
        _page([_row(eid=1), _row(eid=2)], 3),
        _page([_row(eid=3)], 3),
    ])

    events = fetch_history("VCG", session=session, page_size=2)

    assert [e.event_id for e in events] == [1, 2, 3]
    assert session.get.call_count == 2


def test_fetch_history_sorts_oldest_first():
    session = Mock()
    session.get = Mock(return_value=_page([
        _row(eid=2, ex="2026-07-14T00:00:00+07:00"),
        _row(eid=1, ex="2025-05-27T00:00:00+07:00"),
    ], 2))

    events = fetch_history("VCG", session=session)

    assert [e.event_id for e in events] == [1, 2]


def test_fetch_history_stops_on_empty_page_even_if_total_lies():
    session = Mock()
    session.get = Mock(side_effect=[_page([_row()], 99), _page([], 99)])

    events = fetch_history("VCG", session=session, page_size=1)

    assert len(events) == 1
    assert session.get.call_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_corporate_action_parser.py -q -k fetch_history`
Expected: FAIL — `ImportError: cannot import name 'RawEvent'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/app/services/dnse_corporate_actions.py`:

```python
from datetime import date, datetime
from typing import Any, Optional

import requests

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_corporate_action_parser.py -q`
Expected: PASS, 35 tests

- [ ] **Step 5: Commit**

```bash
git add app/services/dnse_corporate_actions.py tests/test_corporate_action_parser.py
git commit -m "feat: fetch paged DNSE corporate-action history"
```

---

### Task 3: Settlement engine

**Files:**
- Create: `backend/app/services/corporate_action_engine.py`
- Test: `backend/tests/test_corporate_action_engine.py`

**Interfaces:**
- Consumes: nothing (deliberately pure — no DB, no network, no ORM types).
- Produces:
  - `@dataclass(frozen=True) Lot(position_id: int, quantity: Decimal, purchase_price: Decimal, purchase_date: date)`
  - `@dataclass(frozen=True) Event(corporate_action_id: int, action_type: str, ex_date: date, amount_per_share: Decimal | None, ratio: Decimal | None)`
  - `@dataclass(frozen=True) Settlement(corporate_action_id, position_id, action_type, qty_before, qty_after, price_before, price_after, shares_added, cash_amount)`
  - `PRICE_SCALE: Decimal` (`Decimal("0.000001")`)
  - `shares_added(quantity: Decimal, ratio: Decimal) -> Decimal`
  - `adjusted_price(price: Decimal, qty_before: Decimal, qty_after: Decimal) -> Decimal`
  - `settle(lot: Lot, events: list[Event]) -> list[Settlement]`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_corporate_action_engine.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_corporate_action_engine.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.corporate_action_engine'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/services/corporate_action_engine.py`:

```python
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
            else:
                if event.ratio is None:
                    continue
                added = shares_added(opening_qty, event.ratio)
                if added <= 0:
                    continue
                added_total += added
                staged.append((event, added, None))

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_corporate_action_engine.py -q`
Expected: PASS, 12 tests

- [ ] **Step 5: Commit**

```bash
git add app/services/corporate_action_engine.py tests/test_corporate_action_engine.py
git commit -m "feat: pure corporate-action settlement engine"
```

---

### Task 4: Models, migration, and Pydantic relaxation

**Files:**
- Create: `backend/app/db/models/corporate_action.py`
- Create: `backend/alembic/versions/d5a91c3e7b20_corporate_actions.py`
- Create: `backend/tests/conftest.py`
- Modify: `backend/app/schemas/portfolio.py:31` and `:33`
- Modify: `backend/app/db/base.py` (register the new models)
- Modify: `backend/tests/test_portfolio_service_mysql.py` (drop the local `db` fixture)
- Test: `backend/tests/test_corporate_action_service_mysql.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `CorporateAction` ORM model, table `corporate_action`.
  - `CorporateActionApplication` ORM model, table `corporate_action_application`.
  - `transactions.transaction_type` accepting `dividend_cash` / `dividend_stock`.
  - `conftest.py` fixture `db` — a MySQL session rolled back after each test.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/conftest.py` (moved verbatim from `test_portfolio_service_mysql.py` so every DB test shares it):

```python
"""Shared fixtures for tests that hit the real MySQL store."""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.settings import settings
from app.db.base import SessionLocal, engine


def mysql_available() -> bool:
    if not settings.database_url.startswith("mysql"):
        return False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


requires_mysql = pytest.mark.skipif(
    not mysql_available(),
    reason=f"MySQL not reachable at {settings.mysql_host}:{settings.mysql_port}",
)


@pytest.fixture
def db():
    """A session whose work is always rolled back.

    The service functions call ``commit()``, which would normally end the outer
    transaction. Binding the session to a connection that already has one open
    nests them, so the outer ``rollback()`` still discards everything — which is
    what keeps these tests off the real 6,349 rows.
    """
    connection = engine.connect()
    outer = connection.begin()
    session = SessionLocal(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()
```

Create `backend/tests/test_corporate_action_service_mysql.py`:

```python
"""Schema-level checks for the corporate-action tables on real MySQL."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import inspect, text

from app.db.base import engine
from app.db.models.corporate_action import CorporateAction, CorporateActionApplication
from app.db.models.portfolio import Transaction
from tests.conftest import requires_mysql

pytestmark = requires_mysql


def test_tables_exist_with_expected_columns():
    cols = {t: {c["name"] for c in inspect(engine).get_columns(t)}
            for t in ("corporate_action", "corporate_action_application")}

    assert {"id", "symbol", "event_id", "name", "action_type", "ex_date",
            "record_date", "pay_date", "amount_per_share", "ratio",
            "tax_withheld_pct", "title", "url", "source", "status",
            "applied_at", "created_at", "updated_at"} <= cols["corporate_action"]
    assert {"id", "corporate_action_id", "position_id", "transaction_id",
            "qty_before", "qty_after", "price_before", "price_after",
            "cash_amount", "created_at"} <= cols["corporate_action_application"]


def test_event_id_is_unique(db):
    def add(event_id):
        db.add(CorporateAction(
            symbol="TST", event_id=event_id, name="Thưởng cổ phiếu",
            action_type="stock", ex_date=date(2026, 1, 1),
            ratio=Decimal("0.1"), title="Thưởng cổ phiếu tỷ lệ 100:10",
            source="dnse_history", status="pending",
        ))
        db.flush()

    add(999000001)
    with pytest.raises(Exception):
        add(999000001)


def test_vietnamese_title_round_trips(db):
    title = "Trả cổ tức năm 2025 bằng cổ phiếu tỷ lệ 100:8"
    ca = CorporateAction(
        symbol="VCG", event_id=999000002, name="Trả cổ tức bằng cổ phiếu",
        action_type="stock", ex_date=date(2026, 7, 14), ratio=Decimal("0.08"),
        title=title, source="dnse_history", status="pending",
    )
    db.add(ca)
    db.flush()
    db.expire(ca)
    assert ca.title == title


@pytest.mark.parametrize("kind", ["dividend_cash", "dividend_stock"])
def test_transactions_accepts_the_new_enum_values(db, kind):
    t = Transaction(
        ticker="TST", transaction_type=kind, quantity=Decimal("10"),
        price=Decimal("0") if kind == "dividend_stock" else Decimal("800"),
        transaction_date=date(2026, 1, 1),
    )
    db.add(t)
    db.flush()
    assert db.execute(
        text("SELECT transaction_type FROM transactions WHERE id = :i"), {"i": t.id}
    ).scalar() == kind


def test_transaction_schema_allows_dividend_rows():
    """The ENUM migration is useless while Pydantic rejects the same values."""
    from app.schemas.portfolio import TransactionCreate

    stock = TransactionCreate(
        ticker="TST", transaction_type="dividend_stock", quantity=Decimal("10"),
        price=Decimal("0"), transaction_date=date(2026, 1, 1),
    )
    assert stock.price == Decimal("0")

    cash = TransactionCreate(
        ticker="TST", transaction_type="dividend_cash", quantity=Decimal("1000"),
        price=Decimal("800"), transaction_date=date(2026, 1, 1),
    )
    assert cash.transaction_type == "dividend_cash"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_corporate_action_service_mysql.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.db.models.corporate_action'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/db/models/corporate_action.py`:

```python
"""Corporate action archive and application ledger.

``corporate_action`` is both what DNSE told us and what we decided to do about
it. ``title`` is stored verbatim because it is the only evidence of what the
parser read — the adjudicating case being TRC's ``tỷ lệ 01:03``, where zero
padding leaves the intended ratio genuinely ambiguous.

``corporate_action_application`` records the before/after of every lot an event
touched, which is the only thing that makes the mutation reversible.
"""
from sqlalchemy import (
    DECIMAL, TIMESTAMP, BigInteger, Column, Date, Enum, ForeignKey, Integer,
    String, UniqueConstraint, text,
)

from app.db.base import Base


class CorporateAction(Base):
    __tablename__ = "corporate_action"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    # DNSE's own id: the idempotency key that makes re-polling free.
    event_id = Column(BigInteger, nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    action_type = Column(Enum("cash", "stock", name="ca_action_type"), nullable=False)
    ex_date = Column(Date, nullable=False, index=True)
    record_date = Column(Date, nullable=True)
    pay_date = Column(Date, nullable=True)
    amount_per_share = Column(DECIMAL(15, 6), nullable=True)  # cash, gross VND
    ratio = Column(DECIMAL(15, 8), nullable=True)             # stock, new/held
    tax_withheld_pct = Column(DECIMAL(5, 4), nullable=True)   # 0.05 for cash
    title = Column(String(500), nullable=False)
    url = Column(String(1024), nullable=True)
    source = Column(
        Enum("dnse_history", "dnse_calendar", "manual", name="ca_source"),
        nullable=False,
    )
    status = Column(
        Enum("pending", "applied", "ignored", "unparsed", name="ca_status"),
        nullable=False,
        server_default=text("'pending'"),
    )
    applied_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))


class CorporateActionApplication(Base):
    __tablename__ = "corporate_action_application"

    id = Column(Integer, primary_key=True, autoincrement=True)
    corporate_action_id = Column(
        Integer, ForeignKey("corporate_action.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Nullable: the lot may be closed and deleted long after this was applied.
    position_id = Column(Integer, nullable=True, index=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)
    qty_before = Column(DECIMAL(15, 6), nullable=False)
    qty_after = Column(DECIMAL(15, 6), nullable=False)
    price_before = Column(DECIMAL(15, 6), nullable=False)
    price_after = Column(DECIMAL(15, 6), nullable=False)
    cash_amount = Column(DECIMAL(20, 6), nullable=True)  # gross, cash only
    created_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        # One event can hit one lot exactly once.
        UniqueConstraint("corporate_action_id", "position_id",
                         name="uq_ca_application_action_position"),
    )
```

In `backend/app/db/base.py`, register the models beside the existing imports so
Alembic autogenerate and `create_all` both see them:

```python
from app.db.models.portfolio import Position, Transaction, InvestmentAmount
from app.db.models.market import Sector, StockSymbol
from app.db.models.corporate_action import CorporateAction, CorporateActionApplication
```

Relax `backend/app/schemas/portfolio.py` — change these two lines inside
`TransactionBase`:

```python
    # dividend rows share this ledger; the ENUM migration is inert without them
    transaction_type: str = Field(..., pattern="^(buy|sell|dividend_cash|dividend_stock)$")
    quantity: Decimal = Field(..., gt=0)
    # ge=0: a stock-dividend row books shares at zero cost
    price: Decimal = Field(..., ge=0)
```

Create `backend/alembic/versions/d5a91c3e7b20_corporate_actions.py`:

```python
"""corporate action archive, application ledger, dividend transaction types

Revision ID: d5a91c3e7b20
Revises: c4d8e1f60b93
"""
from alembic import op
import sqlalchemy as sa


revision = 'd5a91c3e7b20'
down_revision = 'c4d8e1f60b93'
branch_labels = None
depends_on = None

# Widening only: existing 'buy'/'sell' rows are untouched by either direction.
_OLD_TYPES = sa.Enum('buy', 'sell', name='transaction_type')
_NEW_TYPES = sa.Enum('buy', 'sell', 'dividend_cash', 'dividend_stock',
                     name='transaction_type')


def upgrade() -> None:
    op.create_table(
        'corporate_action',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('symbol', sa.String(20), nullable=False),
        sa.Column('event_id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('action_type', sa.Enum('cash', 'stock', name='ca_action_type'),
                  nullable=False),
        sa.Column('ex_date', sa.Date(), nullable=False),
        sa.Column('record_date', sa.Date(), nullable=True),
        sa.Column('pay_date', sa.Date(), nullable=True),
        sa.Column('amount_per_share', sa.DECIMAL(15, 6), nullable=True),
        sa.Column('ratio', sa.DECIMAL(15, 8), nullable=True),
        sa.Column('tax_withheld_pct', sa.DECIMAL(5, 4), nullable=True),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('url', sa.String(1024), nullable=True),
        sa.Column('source', sa.Enum('dnse_history', 'dnse_calendar', 'manual',
                                    name='ca_source'), nullable=False),
        sa.Column('status', sa.Enum('pending', 'applied', 'ignored', 'unparsed',
                                    name='ca_status'), nullable=False,
                  server_default=sa.text("'pending'")),
        sa.Column('applied_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(),
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.TIMESTAMP(),
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('event_id', name='uq_corporate_action_event_id'),
    )
    op.create_index('ix_corporate_action_symbol', 'corporate_action', ['symbol'])
    op.create_index('ix_corporate_action_ex_date', 'corporate_action', ['ex_date'])

    op.create_table(
        'corporate_action_application',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('corporate_action_id', sa.Integer(), nullable=False),
        sa.Column('position_id', sa.Integer(), nullable=True),
        sa.Column('transaction_id', sa.Integer(), nullable=True),
        sa.Column('qty_before', sa.DECIMAL(15, 6), nullable=False),
        sa.Column('qty_after', sa.DECIMAL(15, 6), nullable=False),
        sa.Column('price_before', sa.DECIMAL(15, 6), nullable=False),
        sa.Column('price_after', sa.DECIMAL(15, 6), nullable=False),
        sa.Column('cash_amount', sa.DECIMAL(20, 6), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(),
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['corporate_action_id'], ['corporate_action.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id']),
        sa.UniqueConstraint('corporate_action_id', 'position_id',
                            name='uq_ca_application_action_position'),
    )
    op.create_index('ix_ca_application_corporate_action_id',
                    'corporate_action_application', ['corporate_action_id'])
    op.create_index('ix_ca_application_position_id',
                    'corporate_action_application', ['position_id'])

    op.alter_column('transactions', 'transaction_type',
                    existing_type=_OLD_TYPES, type_=_NEW_TYPES, nullable=False)


def downgrade() -> None:
    # Dividend rows must go before the type can narrow, or the ALTER truncates
    # them to an empty string.
    op.execute("DELETE FROM transactions "
               "WHERE transaction_type IN ('dividend_cash', 'dividend_stock')")
    op.alter_column('transactions', 'transaction_type',
                    existing_type=_NEW_TYPES, type_=_OLD_TYPES, nullable=False)
    op.drop_table('corporate_action_application')
    op.drop_index('ix_corporate_action_ex_date', table_name='corporate_action')
    op.drop_index('ix_corporate_action_symbol', table_name='corporate_action')
    op.drop_table('corporate_action')
```

Finally, delete the now-duplicated `db` fixture and `_mysql_available` /
`pytestmark` block from `backend/tests/test_portfolio_service_mysql.py`,
replacing them with:

```python
from tests.conftest import requires_mysql

pytestmark = requires_mysql
```

- [ ] **Step 4: Run the migration and the tests**

```bash
python -m alembic upgrade head
python -m alembic check
python -m pytest tests/test_corporate_action_service_mysql.py tests/test_portfolio_service_mysql.py -q
```

Expected: migration applies; `alembic check` prints "No new upgrade operations detected"; all tests pass (10 pre-existing + 6 new).

- [ ] **Step 5: Verify the migrated data is untouched**

```bash
python scripts/migrate_portfolio_db_to_mysql.py --sqlite ../portfolio.db --verify
```

Expected: every row count still matches (positions 8, transactions 56, item_value 3960, …).

- [ ] **Step 6: Commit**

```bash
git add app/db/models/corporate_action.py app/db/base.py app/schemas/portfolio.py \
        alembic/versions/d5a91c3e7b20_corporate_actions.py \
        tests/conftest.py tests/test_corporate_action_service_mysql.py \
        tests/test_portfolio_service_mysql.py
git commit -m "feat: corporate action tables and dividend transaction types"
```

---

### Task 5: Sync, list, and manual entry

**Files:**
- Create: `backend/app/services/corporate_action_service.py`
- Create: `backend/app/schemas/corporate_action.py`
- Test: `backend/tests/test_corporate_action_service_mysql.py` (append)

**Interfaces:**
- Consumes: `fetch_history`, `parse_action`, `classify`, `RawEvent` (Tasks 1–2); `CorporateAction` (Task 4).
- Produces:
  - `DEFAULT_CASH_TAX_PCT: Decimal` (`Decimal("0.05")`)
  - `held_symbols(db) -> list[str]`
  - `sync_symbol(db, symbol, *, session=None) -> dict[str, int]` — counts keyed `inserted`, `skipped`, `unparsed`, `ignored`.
  - `sync_all(db, *, session=None) -> dict[str, int]`
  - `list_actions(db, *, status=None, symbol=None) -> list[CorporateAction]`
  - `create_manual(db, payload: ManualDividendCreate) -> CorporateAction`
  - Schemas `CorporateActionOut`, `ManualDividendCreate`, `SyncResult`, `ApplyResult`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_corporate_action_service_mysql.py`:

```python
from unittest.mock import Mock

from app.services import corporate_action_service as cas
from app.services.dnse_corporate_actions import RawEvent
from app.schemas.corporate_action import ManualDividendCreate
from app.schemas.portfolio import PositionCreate
from app.services import portfolio_service


def _raw(event_id, name, title, ex, symbol="TST"):
    return RawEvent(symbol=symbol, event_id=event_id, name=name, title=title,
                    ex_date=date.fromisoformat(ex), record_date=None,
                    pay_date=None, url=None)


def _fake_fetch(events):
    return Mock(side_effect=lambda symbol, **kw: [e for e in events if e.symbol == symbol])


def test_held_symbols_are_distinct_and_upper(db):
    db.execute(text("DELETE FROM positions"))
    for tk in ("aaa", "AAA", "bbb"):
        portfolio_service.create_position(db, PositionCreate(
            ticker=tk, quantity=Decimal("10"), purchase_price=Decimal("10"),
            purchase_date=date(2025, 1, 1)))

    assert cas.held_symbols(db) == ["AAA", "BBB"]


def test_sync_inserts_parsed_events_and_flags_the_rest(db, monkeypatch):
    events = [
        _raw(999100001, "Trả cổ tức bằng tiền mặt",
             "Trả cổ tức năm 2025 bằng tiền 800 đồng/CP", "2026-01-10"),
        _raw(999100002, "Thưởng cổ phiếu", "Thưởng cổ phiếu tỷ lệ 100:10", "2026-02-10"),
        _raw(999100003, "Thưởng cổ phiếu", "Thưởng cổ phiếu nothing here", "2026-03-10"),
        _raw(999100004, "Họp ĐHCĐ bất thường", "Họp ĐHCĐ bất thường 2026", "2026-04-10"),
    ]
    monkeypatch.setattr(cas, "fetch_history", _fake_fetch(events))

    counts = cas.sync_symbol(db, "TST")

    assert counts == {"inserted": 3, "skipped": 0, "unparsed": 1, "ignored": 1}
    rows = {r.event_id: r for r in cas.list_actions(db, symbol="TST", status=None)}
    assert rows[999100001].status == "pending"
    assert rows[999100001].amount_per_share == Decimal("800.000000")
    assert rows[999100001].tax_withheld_pct == Decimal("0.0500")
    assert rows[999100002].ratio == Decimal("0.10000000")
    assert rows[999100002].tax_withheld_pct is None
    assert rows[999100003].status == "unparsed"
    assert rows[999100003].title == "Thưởng cổ phiếu nothing here"
    assert 999100004 not in rows  # meetings are never stored


def test_sync_is_idempotent(db, monkeypatch):
    events = [_raw(999100010, "Thưởng cổ phiếu", "Thưởng cổ phiếu tỷ lệ 100:10",
                   "2026-02-10")]
    monkeypatch.setattr(cas, "fetch_history", _fake_fetch(events))

    first = cas.sync_symbol(db, "TST")
    second = cas.sync_symbol(db, "TST")

    assert first["inserted"] == 1
    assert second == {"inserted": 0, "skipped": 1, "unparsed": 0, "ignored": 0}
    assert len(cas.list_actions(db, symbol="TST", status=None)) == 1


def test_list_actions_defaults_to_pending(db, monkeypatch):
    events = [
        _raw(999100020, "Thưởng cổ phiếu", "Thưởng cổ phiếu tỷ lệ 100:10", "2026-02-10"),
        _raw(999100021, "Thưởng cổ phiếu", "Thưởng cổ phiếu broken", "2026-02-11"),
    ]
    monkeypatch.setattr(cas, "fetch_history", _fake_fetch(events))
    cas.sync_symbol(db, "TST")

    pending = cas.list_actions(db, symbol="TST")
    assert [r.event_id for r in pending] == [999100020]


def test_manual_cash_dividend_is_stored_pending(db):
    ca = cas.create_manual(db, ManualDividendCreate(
        symbol="tst", action_type="cash", ex_date=date(2026, 5, 1),
        amount_per_share=Decimal("1200"), notes="from broker statement"))

    assert ca.status == "pending"
    assert ca.source == "manual"
    assert ca.symbol == "TST"
    assert ca.amount_per_share == Decimal("1200.000000")
    assert ca.tax_withheld_pct == Decimal("0.0500")
    assert ca.event_id < 0  # synthetic, cannot collide with a DNSE id


def test_manual_stock_dividend_requires_a_ratio(db):
    with pytest.raises(ValueError, match="ratio"):
        cas.create_manual(db, ManualDividendCreate(
            symbol="TST", action_type="stock", ex_date=date(2026, 5, 1)))


def test_manual_cash_dividend_requires_an_amount(db):
    with pytest.raises(ValueError, match="amount_per_share"):
        cas.create_manual(db, ManualDividendCreate(
            symbol="TST", action_type="cash", ex_date=date(2026, 5, 1)))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_corporate_action_service_mysql.py -q -k "sync or manual or held or list_actions"`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.corporate_action_service'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/schemas/corporate_action.py`:

```python
"""Request and response models for corporate actions."""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class CorporateActionOut(BaseModel):
    id: int
    symbol: str
    event_id: int
    name: str
    action_type: str
    ex_date: date
    record_date: Optional[date] = None
    pay_date: Optional[date] = None
    amount_per_share: Optional[Decimal] = None
    ratio: Optional[Decimal] = None
    tax_withheld_pct: Optional[Decimal] = None
    title: str
    url: Optional[str] = None
    source: str
    status: str
    applied_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ManualDividendCreate(BaseModel):
    """A dividend entered by hand — for what the feed missed or misparsed."""

    symbol: str = Field(..., min_length=1, max_length=20)
    action_type: Literal["cash", "stock"]
    ex_date: date
    amount_per_share: Optional[Decimal] = Field(default=None, gt=0)
    ratio: Optional[Decimal] = Field(default=None, gt=0)
    tax_withheld_pct: Optional[Decimal] = Field(default=None, ge=0, le=1)
    notes: Optional[str] = None


class SyncResult(BaseModel):
    inserted: int
    skipped: int
    unparsed: int
    ignored: int


class AppliedLot(BaseModel):
    position_id: Optional[int] = None
    qty_before: Decimal
    qty_after: Decimal
    price_before: Decimal
    price_after: Decimal
    shares_added: Decimal
    cash_amount: Optional[Decimal] = None
    transaction_id: Optional[int] = None


class ApplyResult(BaseModel):
    corporate_action_id: int
    # Every action settled in this call. Events sharing an ex-date must settle
    # together (see the engine), so one apply can cover several.
    applied_action_ids: List[int] = []
    status: str
    lots: List[AppliedLot]
    total_shares_added: Decimal
    total_cash_gross: Decimal
```

Create `backend/app/services/corporate_action_service.py`:

```python
"""Corporate actions against the database: capture, review, apply, reverse.

Capture is automatic; application is not. The amount lives in Vietnamese prose,
so a misparse would silently rewrite a cost basis — cheap to review a handful of
events a year, expensive to discover months later.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.corporate_action import CorporateAction
from app.db.models.portfolio import Position
from app.schemas.corporate_action import ManualDividendCreate
from app.services.dnse_corporate_actions import (
    RawEvent, classify, fetch_history, parse_action,
)

# Vietnam withholds 5% PIT on cash dividends. Stored as a rate so the net
# figure can change without a migration; gross stays the factual record.
DEFAULT_CASH_TAX_PCT = Decimal("0.05")


def held_symbols(db: Session) -> List[str]:
    """Distinct tickers with an open lot, upper-cased and sorted."""
    rows = db.execute(select(func.distinct(Position.ticker))).scalars().all()
    return sorted({(t or "").strip().upper() for t in rows if (t or "").strip()})


def _existing_event_ids(db: Session, symbol: str) -> set[int]:
    return set(
        db.execute(
            select(CorporateAction.event_id).where(CorporateAction.symbol == symbol)
        ).scalars().all()
    )


def _store(db: Session, raw: RawEvent, action_type: str) -> None:
    """Insert one event, parsed if possible and flagged if not."""
    parsed = parse_action(raw.name, raw.title)
    db.add(CorporateAction(
        symbol=raw.symbol,
        event_id=raw.event_id,
        name=raw.name,
        action_type=action_type,
        ex_date=raw.ex_date,
        record_date=raw.record_date,
        pay_date=raw.pay_date,
        amount_per_share=parsed.amount_per_share if parsed else None,
        ratio=parsed.ratio if parsed else None,
        tax_withheld_pct=(
            DEFAULT_CASH_TAX_PCT
            if parsed and parsed.action_type == "cash" else None
        ),
        title=raw.title,
        url=raw.url,
        source="dnse_history",
        status="pending" if parsed else "unparsed",
    ))


def sync_symbol(db: Session, symbol: str, *, session: Optional[Any] = None) -> dict:
    """Capture every DNSE event for one symbol. Safe to re-run.

    Events whose ``name`` is not price-affecting are counted and dropped, never
    stored — meetings and shareholder votes would only be noise to review.
    """
    symbol = symbol.strip().upper()
    counts = {"inserted": 0, "skipped": 0, "unparsed": 0, "ignored": 0}
    seen = _existing_event_ids(db, symbol)

    for raw in fetch_history(symbol, session=session):
        action_type = classify(raw.name)
        if action_type is None:
            counts["ignored"] += 1
            continue
        if raw.event_id in seen or raw.ex_date is None:
            counts["skipped"] += 1
            continue

        _store(db, raw, action_type)
        seen.add(raw.event_id)
        counts["inserted"] += 1
        if parse_action(raw.name, raw.title) is None:
            counts["unparsed"] += 1

    db.commit()
    return counts


def sync_all(db: Session, *, session: Optional[Any] = None) -> dict:
    """Sync every held symbol, summing the per-symbol counts."""
    totals = {"inserted": 0, "skipped": 0, "unparsed": 0, "ignored": 0}
    for symbol in held_symbols(db):
        for key, value in sync_symbol(db, symbol, session=session).items():
            totals[key] += value
    return totals


def list_actions(
    db: Session,
    *,
    status: Optional[str] = "pending",
    symbol: Optional[str] = None,
) -> List[CorporateAction]:
    """Events, newest ex-date last. ``status=None`` lists every status."""
    query = select(CorporateAction)
    if status is not None:
        query = query.where(CorporateAction.status == status)
    if symbol:
        query = query.where(CorporateAction.symbol == symbol.strip().upper())
    query = query.order_by(CorporateAction.ex_date, CorporateAction.id)
    return list(db.execute(query).scalars().all())


def _next_manual_event_id(db: Session) -> int:
    """A negative synthetic id, so a manual row can never collide with DNSE's."""
    lowest = db.execute(select(func.min(CorporateAction.event_id))).scalar()
    return min(0, int(lowest or 0)) - 1


def create_manual(db: Session, payload: ManualDividendCreate) -> CorporateAction:
    """Record a dividend by hand, for what the feed missed or misparsed."""
    if payload.action_type == "cash" and payload.amount_per_share is None:
        raise ValueError("amount_per_share is required for a cash dividend")
    if payload.action_type == "stock" and payload.ratio is None:
        raise ValueError("ratio is required for a stock dividend")

    tax = payload.tax_withheld_pct
    if payload.action_type == "cash" and tax is None:
        tax = DEFAULT_CASH_TAX_PCT

    action = CorporateAction(
        symbol=payload.symbol.strip().upper(),
        event_id=_next_manual_event_id(db),
        name="Manual entry",
        action_type=payload.action_type,
        ex_date=payload.ex_date,
        amount_per_share=payload.amount_per_share,
        ratio=payload.ratio,
        tax_withheld_pct=tax if payload.action_type == "cash" else None,
        title=payload.notes or "Manual entry",
        source="manual",
        status="pending",
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return action
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_corporate_action_service_mysql.py -q`
Expected: PASS, 13 tests

- [ ] **Step 5: Commit**

```bash
git add app/services/corporate_action_service.py app/schemas/corporate_action.py \
        tests/test_corporate_action_service_mysql.py
git commit -m "feat: sync, list and manually record corporate actions"
```

---

### Task 6: Apply and unapply

**Files:**
- Modify: `backend/app/services/corporate_action_service.py` (append)
- Test: `backend/tests/test_corporate_action_service_mysql.py` (append)

**Interfaces:**
- Consumes: `settle`, `Lot`, `Event` (Task 3); `CorporateAction`, `CorporateActionApplication` (Task 4); `list_actions` (Task 5).
- Produces:
  - `apply_action(db, corporate_action_id: int) -> ApplyResult` — **applies every pending action sharing the same `(symbol, ex_date)`**, not just the one named.
  - `unapply_action(db, corporate_action_id: int) -> ApplyResult` — reverses only the named action.
  - `ignore_action(db, corporate_action_id: int) -> CorporateAction`

**Why the group, not the single event.** Rule 2 of the spec requires every event
on one ex-date to settle against the same opening quantity. Applying them in
separate calls cannot do that: the first call mutates the lot, so the second
sees the inflated share count. Applied one-at-a-time, PAN 2026-05-29 pays
`3000 × 79,488 = 238,464,000` instead of `3000 × 66,240 = 198,720,000`. So one
apply covers the whole ex-date group, and `applied_action_ids` reports what it
touched.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_corporate_action_service_mysql.py`:

```python
from app.db.models.corporate_action import CorporateAction, CorporateActionApplication


def _pending(db, **kw):
    defaults = dict(
        symbol="TST", event_id=_pending.counter, name="Thưởng cổ phiếu",
        action_type="stock", ex_date=date(2026, 6, 1), ratio=Decimal("0.1"),
        title="Thưởng cổ phiếu tỷ lệ 100:10", source="dnse_history",
        status="pending",
    )
    _pending.counter += 1
    defaults.update(kw)
    ca = CorporateAction(**defaults)
    db.add(ca)
    db.flush()
    return ca


_pending.counter = 999200001


def _lot(db, ticker="TST", qty="1000", price="20", bought="2025-01-01"):
    return portfolio_service.create_position(db, PositionCreate(
        ticker=ticker, quantity=Decimal(qty), purchase_price=Decimal(price),
        purchase_date=date.fromisoformat(bought)))


def test_apply_stock_dividend_mutates_the_lot_and_books_a_transaction(db):
    db.execute(text("DELETE FROM positions"))
    lot = _lot(db, qty="10900", price="15.46", bought="2026-03-02")
    ca = _pending(db, ex_date=date(2026, 6, 17))

    result = cas.apply_action(db, ca.id)

    assert result.status == "applied"
    assert result.total_shares_added == Decimal("1090")
    refreshed = portfolio_service.get_position(db, lot.id)
    assert refreshed.quantity == Decimal("11990.000000")
    assert refreshed.purchase_price == Decimal("14.054545")

    tx = db.execute(text(
        "SELECT transaction_type, quantity, price FROM transactions WHERE id = :i"
    ), {"i": result.lots[0].transaction_id}).one()
    assert tx.transaction_type == "dividend_stock"
    assert tx.quantity == Decimal("1090.000000")
    assert tx.price == Decimal("0.000000")


def test_apply_cash_dividend_leaves_the_lot_alone(db):
    db.execute(text("DELETE FROM positions"))
    lot = _lot(db, qty="1000", price="20")
    ca = _pending(db, name="Trả cổ tức bằng tiền mặt", action_type="cash",
                  ratio=None, amount_per_share=Decimal("800"),
                  tax_withheld_pct=Decimal("0.05"),
                  title="Trả cổ tức năm 2025 bằng tiền 800 đồng/CP")

    result = cas.apply_action(db, ca.id)

    assert result.total_cash_gross == Decimal("800000")
    assert result.total_shares_added == Decimal("0")
    refreshed = portfolio_service.get_position(db, lot.id)
    assert refreshed.quantity == Decimal("1000.000000")
    assert refreshed.purchase_price == Decimal("20.000000")


def test_apply_skips_lots_bought_after_the_ex_date(db):
    db.execute(text("DELETE FROM positions"))
    _lot(db, qty="1000", price="20", bought="2026-01-01")
    _lot(db, qty="500", price="21", bought="2026-12-01")
    ca = _pending(db, ex_date=date(2026, 6, 1))

    result = cas.apply_action(db, ca.id)

    assert len(result.lots) == 1
    assert result.total_shares_added == Decimal("100")


def test_apply_twice_is_rejected(db):
    db.execute(text("DELETE FROM positions"))
    _lot(db)
    ca = _pending(db)
    cas.apply_action(db, ca.id)

    with pytest.raises(ValueError, match="already applied"):
        cas.apply_action(db, ca.id)


def test_apply_an_unparsed_event_is_rejected(db):
    ca = _pending(db, status="unparsed", ratio=None)
    with pytest.raises(ValueError, match="unparsed"):
        cas.apply_action(db, ca.id)


def test_apply_with_no_eligible_lot_still_marks_applied(db):
    db.execute(text("DELETE FROM positions"))
    ca = _pending(db)

    result = cas.apply_action(db, ca.id)

    assert result.lots == []
    assert result.status == "applied"


def test_unapply_restores_quantity_and_price_and_removes_the_transaction(db):
    db.execute(text("DELETE FROM positions"))
    lot = _lot(db, qty="10900", price="15.46", bought="2026-03-02")
    ca = _pending(db, ex_date=date(2026, 6, 17))
    applied = cas.apply_action(db, ca.id)
    tx_id = applied.lots[0].transaction_id

    cas.unapply_action(db, ca.id)

    refreshed = portfolio_service.get_position(db, lot.id)
    assert refreshed.quantity == Decimal("10900.000000")
    assert refreshed.purchase_price == Decimal("15.460000")
    assert db.get(CorporateAction, ca.id).status == "pending"
    assert db.execute(text("SELECT COUNT(*) FROM transactions WHERE id = :i"),
                      {"i": tx_id}).scalar() == 0
    assert db.execute(select(func.count()).select_from(CorporateActionApplication)
                      .where(CorporateActionApplication.corporate_action_id == ca.id)
                      ).scalar() == 0


def test_unapply_a_pending_event_is_rejected(db):
    ca = _pending(db)
    with pytest.raises(ValueError, match="not applied"):
        cas.unapply_action(db, ca.id)


def test_unapplying_the_cash_half_leaves_the_bonus_in_place(db):
    """A cash dividend never moved the lot, so reversing it must not either."""
    lot, bonus, cash_ca = _pan_same_day(db)
    cas.apply_action(db, bonus.id)

    cas.unapply_action(db, cash_ca.id)

    refreshed = portfolio_service.get_position(db, lot.id)
    assert refreshed.quantity == Decimal("79488.000000")
    assert refreshed.purchase_price == Decimal("18.891667")
    assert db.get(CorporateAction, bonus.id).status == "applied"
    assert db.get(CorporateAction, cash_ca.id).status == "pending"


def test_unapplying_the_bonus_half_restores_the_lot(db):
    lot, bonus, cash_ca = _pan_same_day(db)
    cas.apply_action(db, bonus.id)

    cas.unapply_action(db, bonus.id)

    refreshed = portfolio_service.get_position(db, lot.id)
    assert refreshed.quantity == Decimal("66240.000000")
    assert refreshed.purchase_price == Decimal("22.670000")


def test_ignore_marks_the_event_and_touches_nothing(db):
    db.execute(text("DELETE FROM positions"))
    lot = _lot(db, qty="1000", price="20")
    ca = _pending(db)

    assert cas.ignore_action(db, ca.id).status == "ignored"
    assert portfolio_service.get_position(db, lot.id).quantity == Decimal("1000.000000")


def _pan_same_day(db):
    """PAN 2026-05-29: a bonus and a cash dividend on one ex-date."""
    db.execute(text("DELETE FROM positions"))
    lot = _lot(db, ticker="PAN", qty="66240", price="22.67", bought="2026-01-01")
    bonus = _pending(db, symbol="PAN", ex_date=date(2026, 5, 29), ratio=Decimal("0.2"),
                     title="Thưởng cổ phiếu tỷ lệ 100:20")
    cash_ca = _pending(db, symbol="PAN", ex_date=date(2026, 5, 29),
                       name="Trả cổ tức bằng tiền mặt", action_type="cash", ratio=None,
                       amount_per_share=Decimal("3000"),
                       tax_withheld_pct=Decimal("0.05"),
                       title="Trả cổ tức năm 2026 bằng tiền 3000 đồng/CP")
    return lot, bonus, cash_ca


def test_applying_one_event_settles_its_whole_ex_date_group(db):
    """The cash must pay on the pre-bonus 66,240, not the post-bonus 79,488.

    Applying the two separately cannot achieve that — the first call has already
    moved the share count — so one apply covers the ex-date group.
    """
    lot, bonus, cash_ca = _pan_same_day(db)

    result = cas.apply_action(db, bonus.id)

    assert sorted(result.applied_action_ids) == sorted([bonus.id, cash_ca.id])
    assert result.total_cash_gross == Decimal("198720000")
    assert result.total_shares_added == Decimal("13248")
    refreshed = portfolio_service.get_position(db, lot.id)
    assert refreshed.quantity == Decimal("79488.000000")
    assert refreshed.purchase_price == Decimal("18.891667")
    assert db.get(CorporateAction, cash_ca.id).status == "applied"


def test_applying_the_cash_half_first_gives_the_same_result(db):
    """Whichever sibling the user clicks, the group settles identically."""
    lot, bonus, cash_ca = _pan_same_day(db)

    result = cas.apply_action(db, cash_ca.id)

    assert result.total_cash_gross == Decimal("198720000")
    assert portfolio_service.get_position(db, lot.id).quantity == Decimal("79488.000000")
    assert db.get(CorporateAction, bonus.id).status == "applied"


def test_applying_a_sibling_after_its_group_is_rejected(db):
    """The group already ran; the second click must not double-apply."""
    _lot_bonus_cash = _pan_same_day(db)
    _, bonus, cash_ca = _lot_bonus_cash
    cas.apply_action(db, bonus.id)

    with pytest.raises(ValueError, match="already applied"):
        cas.apply_action(db, cash_ca.id)


def test_a_different_ex_date_is_not_dragged_into_the_group(db):
    db.execute(text("DELETE FROM positions"))
    _lot(db, ticker="TST", qty="1000", price="20", bought="2025-01-01")
    first = _pending(db, ex_date=date(2026, 6, 1), ratio=Decimal("0.1"))
    later = _pending(db, ex_date=date(2026, 9, 1), ratio=Decimal("0.1"))

    result = cas.apply_action(db, first.id)

    assert result.applied_action_ids == [first.id]
    assert db.get(CorporateAction, later.id).status == "pending"


def test_another_symbol_on_the_same_ex_date_is_not_dragged_in(db):
    db.execute(text("DELETE FROM positions"))
    _lot(db, ticker="TST", qty="1000", price="20", bought="2025-01-01")
    mine = _pending(db, symbol="TST", ex_date=date(2026, 6, 1), ratio=Decimal("0.1"))
    other = _pending(db, symbol="OTH", ex_date=date(2026, 6, 1), ratio=Decimal("0.1"))

    result = cas.apply_action(db, mine.id)

    assert result.applied_action_ids == [mine.id]
    assert db.get(CorporateAction, other.id).status == "pending"


def test_an_unparsed_sibling_does_not_block_the_group(db):
    """It cannot be settled, so it stays for review rather than failing the group."""
    db.execute(text("DELETE FROM positions"))
    _lot(db, ticker="TST", qty="1000", price="20", bought="2025-01-01")
    good = _pending(db, ex_date=date(2026, 6, 1), ratio=Decimal("0.1"))
    bad = _pending(db, ex_date=date(2026, 6, 1), ratio=None, status="unparsed",
                   title="Thưởng cổ phiếu nothing here")

    result = cas.apply_action(db, good.id)

    assert result.applied_action_ids == [good.id]
    assert db.get(CorporateAction, bad.id).status == "unparsed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_corporate_action_service_mysql.py -q -k "apply or unapply or ignore"`
Expected: FAIL — `AttributeError: module 'app.services.corporate_action_service' has no attribute 'apply_action'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/app/services/corporate_action_service.py`:

```python
from app.db.models.corporate_action import CorporateActionApplication
from app.db.models.portfolio import Transaction
from app.schemas.corporate_action import AppliedLot, ApplyResult
from app.services.corporate_action_engine import Event, Lot, settle


def _load(db: Session, corporate_action_id: int) -> CorporateAction:
    action = db.get(CorporateAction, corporate_action_id)
    if action is None:
        raise ValueError(f"Corporate action {corporate_action_id} not found")
    return action


def _ex_date_group(db: Session, action: CorporateAction) -> List[CorporateAction]:
    """Every pending action sharing this one's symbol and ex-date.

    The group, not the single event, is the unit of application: rule 2 requires
    them all to settle against one opening quantity, which is impossible once an
    earlier apply has already moved the share count.

    ``unparsed`` siblings are left out — they carry no amount to settle — and
    stay pending for manual entry rather than failing the whole group.
    """
    return list(db.execute(
        select(CorporateAction)
        .where(
            CorporateAction.symbol == action.symbol,
            CorporateAction.ex_date == action.ex_date,
            CorporateAction.status == "pending",
        )
        .order_by(CorporateAction.id)
    ).scalars().all())


def _as_event(action: CorporateAction) -> Event:
    return Event(
        corporate_action_id=action.id,
        action_type=action.action_type,
        ex_date=action.ex_date,
        amount_per_share=action.amount_per_share,
        ratio=action.ratio,
    )


def apply_action(db: Session, corporate_action_id: int) -> ApplyResult:
    """Apply an event — and its ex-date siblings — to every eligible lot.

    All of it lands in one transaction: the lot mutations, the ledger rows, the
    application records and the status changes commit together or not at all,
    the same all-or-nothing shape ``close_position`` uses. A half-applied
    dividend would leave a cost basis nobody can reconstruct.

    An event with no eligible lot is still marked applied. It genuinely has
    nothing to do, and leaving it pending would mean reviewing it forever.
    """
    action = _load(db, corporate_action_id)
    if action.status == "applied":
        raise ValueError(f"Corporate action {corporate_action_id} is already applied")
    if action.status == "unparsed":
        raise ValueError(
            f"Corporate action {corporate_action_id} is unparsed; "
            "record it manually instead of guessing an amount"
        )
    if action.status == "ignored":
        raise ValueError(f"Corporate action {corporate_action_id} is ignored")

    try:
        group = _ex_date_group(db, action)
        events = [_as_event(a) for a in group]
        by_id = {a.id: a for a in group}

        lots = (
            db.query(Position)
            .filter(Position.ticker == action.symbol)
            .with_for_update()
            .all()
        )

        applied: List[AppliedLot] = []
        for position in lots:
            for s in settle(
                Lot(position.id, position.quantity, position.purchase_price,
                    position.purchase_date),
                events,
            ):
                source = by_id[s.corporate_action_id]
                transaction = Transaction(
                    ticker=action.symbol,
                    transaction_type=(
                        "dividend_cash" if s.action_type == "cash" else "dividend_stock"
                    ),
                    quantity=(
                        s.qty_before if s.action_type == "cash" else s.shares_added
                    ),
                    price=(
                        source.amount_per_share
                        if s.action_type == "cash" else Decimal(0)
                    ),
                    transaction_date=source.ex_date,
                    fees=Decimal(0),
                    notes=f"{source.name}: {source.title}",
                )
                db.add(transaction)
                db.flush()

                # Only a stock event moves the lot. A cash settlement reports
                # ``qty_after == qty_before`` by design, so writing it blindly
                # would undo a bonus applied earlier in the same group — PAN
                # 2026-05-29 would land back on 66,240. Where several stock
                # events share an ex-date they all carry the group's final
                # quantity, so writing each is idempotent.
                if s.shares_added > 0:
                    position.quantity = s.qty_after
                    position.purchase_price = s.price_after

                db.add(CorporateActionApplication(
                    corporate_action_id=s.corporate_action_id,
                    position_id=position.id,
                    transaction_id=transaction.id,
                    qty_before=s.qty_before,
                    qty_after=s.qty_after,
                    price_before=s.price_before,
                    price_after=s.price_after,
                    cash_amount=s.cash_amount,
                ))
                applied.append(AppliedLot(
                    position_id=position.id,
                    qty_before=s.qty_before, qty_after=s.qty_after,
                    price_before=s.price_before, price_after=s.price_after,
                    shares_added=s.shares_added, cash_amount=s.cash_amount,
                    transaction_id=transaction.id,
                ))

        applied_at = datetime.now()
        for member in group:
            member.status = "applied"
            member.applied_at = applied_at
        db.commit()
    except Exception:
        db.rollback()
        raise

    return ApplyResult(
        corporate_action_id=action.id,
        applied_action_ids=[a.id for a in group],
        status="applied",
        lots=applied,
        total_shares_added=sum((l.shares_added for l in applied), Decimal(0)),
        total_cash_gross=sum((l.cash_amount or Decimal(0) for l in applied), Decimal(0)),
    )


def unapply_action(db: Session, corporate_action_id: int) -> ApplyResult:
    """Reverse an applied event from its own application records.

    The records carry the before values, so this restores them exactly rather
    than recomputing an inverse — dividing by ``1+ratio`` would not land back on
    the original after truncation.
    """
    action = _load(db, corporate_action_id)
    if action.status != "applied":
        raise ValueError(f"Corporate action {corporate_action_id} is not applied")

    try:
        records = list(db.execute(
            select(CorporateActionApplication).where(
                CorporateActionApplication.corporate_action_id == action.id
            )
        ).scalars().all())

        reverted: List[AppliedLot] = []
        for record in records:
            # A record that did not move the lot (any cash dividend) must not
            # write to it. Restoring its ``qty_before`` would revert a stock
            # event from the same ex-date that is still applied.
            moved = (
                record.qty_after != record.qty_before
                or record.price_after != record.price_before
            )
            if moved and record.position_id is not None:
                position = (
                    db.query(Position)
                    .filter(Position.id == record.position_id)
                    .with_for_update()
                    .first()
                )
                if position is not None:
                    position.quantity = record.qty_before
                    position.purchase_price = record.price_before

            if record.transaction_id is not None:
                transaction = db.get(Transaction, record.transaction_id)
                if transaction is not None:
                    db.delete(transaction)

            reverted.append(AppliedLot(
                position_id=record.position_id,
                qty_before=record.qty_after, qty_after=record.qty_before,
                price_before=record.price_after, price_after=record.price_before,
                shares_added=record.qty_before - record.qty_after,
                cash_amount=record.cash_amount,
                transaction_id=record.transaction_id,
            ))
            db.delete(record)

        action.status = "pending"
        action.applied_at = None
        db.commit()
    except Exception:
        db.rollback()
        raise

    return ApplyResult(
        corporate_action_id=action.id,
        status="pending",
        lots=reverted,
        total_shares_added=Decimal(0),
        total_cash_gross=Decimal(0),
    )


def ignore_action(db: Session, corporate_action_id: int) -> CorporateAction:
    """Mark an event as deliberately not applicable. Touches no lot."""
    action = _load(db, corporate_action_id)
    if action.status == "applied":
        raise ValueError(
            f"Corporate action {corporate_action_id} is applied; unapply it first"
        )
    action.status = "ignored"
    db.commit()
    db.refresh(action)
    return action
```

Note on the `quantity` expression for the ledger row — write it explicitly
rather than with the `and`/`or` trick above, which is easy to misread:

```python
                    quantity=(
                        s.qty_before if s.action_type == "cash" else s.shares_added
                    ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_corporate_action_service_mysql.py -q`
Expected: PASS, 30 tests

- [ ] **Step 5: Commit**

```bash
git add app/services/corporate_action_service.py tests/test_corporate_action_service_mysql.py
git commit -m "feat: apply, unapply and ignore corporate actions"
```

---

### Task 7: Dividend income in the portfolio summary

**Files:**
- Modify: `backend/app/services/portfolio_service.py` (append aggregate, wire into `get_portfolio_summary`)
- Modify: `backend/app/schemas/portfolio.py` (`PortfolioSummary`)
- Test: `backend/tests/test_portfolio_service_mysql.py` (append)

**Interfaces:**
- Consumes: `CorporateAction`, `CorporateActionApplication` (Task 4); the `dividend_cash` rows written by `apply_action` (Task 6).
- Produces:
  - `_calculate_dividend_income(db) -> tuple[Decimal, Decimal]` — `(gross, net)`.
  - `PortfolioSummary.total_dividend_income_gross`, `PortfolioSummary.total_dividend_income`.
  - `total_realized_pl` = trading gains + net dividend income.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_portfolio_service_mysql.py`:

```python
def test_dividend_income_is_gross_and_net(db):
    from app.db.models.corporate_action import (
        CorporateAction, CorporateActionApplication,
    )

    db.execute(text("DELETE FROM corporate_action_application"))
    db.execute(text("DELETE FROM corporate_action"))

    ca = CorporateAction(
        symbol="TST", event_id=999300001, name="Trả cổ tức bằng tiền mặt",
        action_type="cash", ex_date=date(2026, 1, 1),
        amount_per_share=Decimal("800"), tax_withheld_pct=Decimal("0.05"),
        title="Trả cổ tức năm 2025 bằng tiền 800 đồng/CP",
        source="dnse_history", status="applied",
    )
    db.add(ca)
    db.flush()
    db.add(CorporateActionApplication(
        corporate_action_id=ca.id, position_id=None, transaction_id=None,
        qty_before=Decimal("1000"), qty_after=Decimal("1000"),
        price_before=Decimal("20"), price_after=Decimal("20"),
        cash_amount=Decimal("800000"),
    ))
    db.flush()

    gross, net = svc._calculate_dividend_income(db)
    assert gross == Decimal("800000")
    assert net == Decimal("760000")


def test_dividend_income_is_zero_with_no_events(db):
    db.execute(text("DELETE FROM corporate_action_application"))
    assert svc._calculate_dividend_income(db) == (Decimal(0), Decimal(0))


def test_stock_events_contribute_no_income(db):
    from app.db.models.corporate_action import (
        CorporateAction, CorporateActionApplication,
    )

    db.execute(text("DELETE FROM corporate_action_application"))
    db.execute(text("DELETE FROM corporate_action"))
    ca = CorporateAction(
        symbol="TST", event_id=999300002, name="Thưởng cổ phiếu",
        action_type="stock", ex_date=date(2026, 1, 1), ratio=Decimal("0.1"),
        title="Thưởng cổ phiếu tỷ lệ 100:10", source="dnse_history",
        status="applied",
    )
    db.add(ca)
    db.flush()
    db.add(CorporateActionApplication(
        corporate_action_id=ca.id, position_id=None, transaction_id=None,
        qty_before=Decimal("1000"), qty_after=Decimal("1100"),
        price_before=Decimal("20"), price_after=Decimal("18.181818"),
        cash_amount=None,
    ))
    db.flush()

    assert svc._calculate_dividend_income(db) == (Decimal(0), Decimal(0))


async def test_summary_adds_net_dividend_income_to_realized_pl(db, monkeypatch):
    from app.db.models.corporate_action import (
        CorporateAction, CorporateActionApplication,
    )

    async def fake_price(ticker: str):
        return Decimal("30")

    monkeypatch.setattr(svc, "get_current_price", fake_price)
    db.execute(text("DELETE FROM corporate_action_application"))
    db.execute(text("DELETE FROM corporate_action"))
    db.execute(text("DELETE FROM transactions"))
    db.execute(text("DELETE FROM positions"))

    svc.create_position(db, _position(ticker="SUM", qty="10", price="20"))
    ca = CorporateAction(
        symbol="SUM", event_id=999300003, name="Trả cổ tức bằng tiền mặt",
        action_type="cash", ex_date=date(2026, 1, 1),
        amount_per_share=Decimal("800"), tax_withheld_pct=Decimal("0.05"),
        title="Trả cổ tức năm 2025 bằng tiền 800 đồng/CP",
        source="dnse_history", status="applied",
    )
    db.add(ca)
    db.flush()
    db.add(CorporateActionApplication(
        corporate_action_id=ca.id, position_id=None, transaction_id=None,
        qty_before=Decimal("10"), qty_after=Decimal("10"),
        price_before=Decimal("20"), price_after=Decimal("20"),
        cash_amount=Decimal("8000"),
    ))
    db.flush()

    summary = await svc.get_portfolio_summary(db)

    assert summary.total_dividend_income_gross == Decimal("8000")
    assert summary.total_dividend_income == Decimal("7600")
    # no sells, so realized P/L is the dividend income alone
    assert summary.total_realized_pl == Decimal("7600")
    assert summary.total_value == Decimal("300")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_portfolio_service_mysql.py -q -k dividend`
Expected: FAIL — `AttributeError: module 'app.services.portfolio_service' has no attribute '_calculate_dividend_income'`

- [ ] **Step 3: Write minimal implementation**

In `backend/app/schemas/portfolio.py`, add two fields to `PortfolioSummary`:

```python
class PortfolioSummary(BaseModel):
    total_value: Decimal
    total_invested: Decimal
    total_profit_loss: Decimal
    total_profit_loss_pct: Decimal
    total_realized_pl: Decimal
    # Gross is the factual record; the headline figure is net of withholding.
    total_dividend_income_gross: Decimal = Decimal(0)
    total_dividend_income: Decimal = Decimal(0)
    positions: List[Position]
```

Append to `backend/app/services/portfolio_service.py`:

```python
def _calculate_dividend_income(db: Session) -> tuple[Decimal, Decimal]:
    """Cash dividend income, ``(gross, net)``.

    Read from the application ledger rather than the ``transactions`` rows,
    because the withholding rate lives on the event and the ledger already
    holds the gross amount per lot. Stock events carry a NULL ``cash_amount``
    and so contribute nothing.
    """
    from app.db.models.corporate_action import (
        CorporateAction,
        CorporateActionApplication,
    )

    cash = func.coalesce(CorporateActionApplication.cash_amount, 0)
    kept = 1 - func.coalesce(CorporateAction.tax_withheld_pct, 0)

    row = db.execute(
        select(
            func.coalesce(func.sum(cash), 0),
            func.coalesce(func.sum(cash * kept), 0),
        )
        .select_from(CorporateActionApplication)
        .join(
            CorporateAction,
            CorporateAction.id == CorporateActionApplication.corporate_action_id,
        )
        .where(CorporateAction.action_type == "cash")
    ).one()

    return Decimal(str(row[0])), Decimal(str(row[1]))
```

Then rewrite the tail of `get_portfolio_summary`:

```python
    # Realized P/L = trading gains on sells + net dividend income
    trading_pl = _calculate_realized_pl(db)
    dividend_gross, dividend_net = _calculate_dividend_income(db)

    return PortfolioSummary(
        total_value=total_value,
        total_invested=total_invested,
        total_profit_loss=total_profit_loss,
        total_profit_loss_pct=total_profit_loss_pct,
        total_realized_pl=trading_pl + dividend_net,
        total_dividend_income_gross=dividend_gross,
        total_dividend_income=dividend_net,
        positions=positions,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_portfolio_service_mysql.py -q`
Expected: PASS, 14 tests

- [ ] **Step 5: Commit**

```bash
git add app/services/portfolio_service.py app/schemas/portfolio.py \
        tests/test_portfolio_service_mysql.py
git commit -m "feat: report gross and net dividend income in the summary"
```

---

### Task 8: HTTP routes

**Files:**
- Create: `backend/app/api/v1/routes/corporate_actions.py`
- Modify: wherever routers are registered (grep for `portfolio.router` under `backend/app`)
- Test: `backend/tests/test_corporate_action_routes.py`

**Interfaces:**
- Consumes: everything in `corporate_action_service` (Tasks 5–6) and the schemas from Task 5.
- Produces: `router` — six endpoints under prefix `/portfolio`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_corporate_action_routes.py`:

```python
"""Route wiring for corporate actions. Service behaviour is tested elsewhere."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import requires_mysql

pytestmark = requires_mysql

client = TestClient(app)
BASE = "/api/v1/portfolio/corporate-actions"


def test_list_returns_200_and_a_list():
    response = client.get(BASE)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_list_accepts_status_and_symbol_filters():
    assert client.get(BASE, params={"status": "applied", "symbol": "VCG"}).status_code == 200
    assert client.get(BASE, params={"status": "all"}).status_code == 200


def test_apply_unknown_id_is_404():
    assert client.post(f"{BASE}/99999999/apply").status_code == 404


def test_unapply_unknown_id_is_404():
    assert client.post(f"{BASE}/99999999/unapply").status_code == 404


def test_ignore_unknown_id_is_404():
    assert client.post(f"{BASE}/99999999/ignore").status_code == 404


def test_manual_dividend_validation_rejects_a_missing_amount():
    response = client.post("/api/v1/portfolio/dividends", json={
        "symbol": "TST", "action_type": "cash", "ex_date": "2026-01-01",
    })
    assert response.status_code == 400


def test_manual_dividend_rejects_an_unknown_action_type():
    response = client.post("/api/v1/portfolio/dividends", json={
        "symbol": "TST", "action_type": "rights", "ex_date": "2026-01-01",
    })
    assert response.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_corporate_action_routes.py -q`
Expected: FAIL — 404 on the list endpoint, since the router is not registered

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/api/v1/routes/corporate_actions.py`:

```python
"""Corporate action endpoints.

Kept in their own module rather than swelling ``routes/portfolio.py``, but
mounted on the same ``/portfolio`` prefix so the URL surface stays one thing.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.schemas.corporate_action import (
    ApplyResult, CorporateActionOut, ManualDividendCreate, SyncResult,
)
from app.services import corporate_action_service as service

router = APIRouter(prefix="/portfolio", tags=["corporate-actions"])


@router.post("/corporate-actions/sync", response_model=SyncResult)
def sync_corporate_actions(db: Session = Depends(get_db)) -> SyncResult:
    """Capture DNSE history for every held ticker. Safe to call repeatedly."""
    return SyncResult(**service.sync_all(db))


@router.get("/corporate-actions", response_model=List[CorporateActionOut])
def list_corporate_actions(
    status: Optional[str] = Query(default="pending",
                                  description="pending|applied|ignored|unparsed|all"),
    symbol: Optional[str] = None,
    db: Session = Depends(get_db),
) -> List[CorporateActionOut]:
    return service.list_actions(
        db, status=None if status == "all" else status, symbol=symbol
    )


@router.post("/corporate-actions/{action_id}/apply", response_model=ApplyResult)
def apply_corporate_action(action_id: int, db: Session = Depends(get_db)) -> ApplyResult:
    try:
        return service.apply_action(db, action_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404 if "not found" in str(exc) else 409, detail=str(exc)
        )


@router.post("/corporate-actions/{action_id}/unapply", response_model=ApplyResult)
def unapply_corporate_action(action_id: int, db: Session = Depends(get_db)) -> ApplyResult:
    try:
        return service.unapply_action(db, action_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404 if "not found" in str(exc) else 409, detail=str(exc)
        )


@router.post("/corporate-actions/{action_id}/ignore", response_model=CorporateActionOut)
def ignore_corporate_action(action_id: int, db: Session = Depends(get_db)):
    try:
        return service.ignore_action(db, action_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404 if "not found" in str(exc) else 409, detail=str(exc)
        )


@router.post("/dividends", response_model=CorporateActionOut)
def create_manual_dividend(
    payload: ManualDividendCreate, db: Session = Depends(get_db)
):
    """Record a dividend by hand — for what the feed missed or misparsed."""
    try:
        return service.create_manual(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
```

Register it next to the existing portfolio router. Find the spot with:

```bash
grep -rn "portfolio.router\|include_router" app/api app/main.py | head
```

then add the import and one `include_router` call mirroring the existing style,
for example:

```python
from app.api.v1.routes import corporate_actions

api_router.include_router(corporate_actions.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_corporate_action_routes.py -q`
Expected: PASS, 7 tests

- [ ] **Step 5: Verify against the running container**

```bash
docker compose up -d --force-recreate backend
curl -s "http://localhost:8000/api/v1/portfolio/corporate-actions?status=all" | head -c 200
```

Expected: HTTP 200 and a JSON array (empty until Task 10 syncs).

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/routes/corporate_actions.py tests/test_corporate_action_routes.py
git add -u app/api
git commit -m "feat: corporate action endpoints"
```

---

### Task 9: Daily sync task

**Files:**
- Create: `backend/tasks/sync_corporate_actions.py`
- Test: `backend/tests/test_sync_corporate_actions_task.py`

**Interfaces:**
- Consumes: `sync_all` (Task 5), `SessionLocal` from `app.db.base`.
- Produces: `main() -> dict[str, int]` and a `__main__` entry point.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_sync_corporate_actions_task.py`:

```python
from unittest.mock import Mock

from tasks import sync_corporate_actions as task


def test_main_syncs_and_closes_the_session(monkeypatch):
    session = Mock()
    monkeypatch.setattr(task, "SessionLocal", Mock(return_value=session))
    monkeypatch.setattr(task, "sync_all",
                        Mock(return_value={"inserted": 2, "skipped": 1,
                                           "unparsed": 0, "ignored": 5}))

    result = task.main()

    assert result == {"inserted": 2, "skipped": 1, "unparsed": 0, "ignored": 5}
    session.close.assert_called_once()


def test_main_closes_the_session_even_when_sync_raises(monkeypatch):
    session = Mock()
    monkeypatch.setattr(task, "SessionLocal", Mock(return_value=session))
    monkeypatch.setattr(task, "sync_all", Mock(side_effect=RuntimeError("DNSE down")))

    try:
        task.main()
    except RuntimeError:
        pass

    session.close.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sync_corporate_actions_task.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tasks.sync_corporate_actions'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/tasks/sync_corporate_actions.py`:

```python
"""Daily capture of DNSE corporate actions for held tickers.

Run from ``backend/``::

    python -m tasks.sync_corporate_actions

Capture only — nothing is applied. New events land as ``pending`` (or
``unparsed``) for review at
``GET /api/v1/portfolio/corporate-actions``.

Daily is ample: ex-dates are announced well ahead, and because the DNSE history
endpoint returns a complete series, a missed run self-heals on the next one.
"""
from __future__ import annotations

import logging

from app.db.base import SessionLocal
from app.services.corporate_action_service import sync_all

logger = logging.getLogger(__name__)


def main() -> dict:
    db = SessionLocal()
    try:
        counts = sync_all(db)
        logger.info(
            "corporate action sync: %s inserted, %s already known, "
            "%s unparsed, %s not price-affecting",
            counts["inserted"], counts["skipped"],
            counts["unparsed"], counts["ignored"],
        )
        return counts
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sync_corporate_actions_task.py -q`
Expected: PASS, 2 tests

- [ ] **Step 5: Commit**

```bash
git add tasks/sync_corporate_actions.py tests/test_sync_corporate_actions_task.py
git commit -m "feat: daily corporate action sync task"
```

---

### Task 10: Backfill the eight stale lots

This task changes real data. It runs through the same endpoints as the daily
job — nothing bespoke — and stops for approval before applying.

**Files:**
- Test: `backend/tests/test_corporate_action_backfill.py`
- No production code. If a bug surfaces here, fix it in the owning task's file
  and add the failing case to that task's test file.

**Interfaces:**
- Consumes: the whole stack, Tasks 1–8.
- Produces: corrected `positions` rows, `dividend_*` transactions, and an
  `applied` corporate action per event.

- [ ] **Step 1: Write the golden test**

Create `backend/tests/test_corporate_action_backfill.py`:

```python
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
EXPECTED_GROSS_CASH = Decimal("307044000")
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
```

- [ ] **Step 2: Run the golden test**

Run: `python -m pytest tests/test_corporate_action_backfill.py -q`
Expected: PASS. It hits the live DNSE API and rolls everything back.

If any assertion fails, **stop**. Fix the owning module and add the failing case
to that module's test file. Do not adjust the expected numbers — they are the
specification.

- [ ] **Step 3: Run the whole suite**

```bash
python -m pytest tests/ -q -p no:cacheprovider --ignore=tests/test_block_episodes.py
python -m alembic check
```

Expected: everything passes (`test_block_episodes.py` is excluded — it imports a
module that does not exist in the repo and was already broken). `alembic check`
reports no new operations.

- [ ] **Step 4: Capture the current state, then sync for real**

```bash
docker compose exec -T backend python -c "
from sqlalchemy import text
from app.db.base import engine
with engine.connect() as c:
    for r in c.execute(text('select id,ticker,quantity,purchase_price from positions order by id')):
        print(r)
"
curl -s -X POST http://localhost:8000/api/v1/portfolio/corporate-actions/sync
curl -s "http://localhost:8000/api/v1/portfolio/corporate-actions?status=pending"
curl -s "http://localhost:8000/api/v1/portfolio/corporate-actions?status=unparsed"
```

Save that "before" listing. Review the pending list against the spec's per-lot
table.

- [ ] **Step 5: Stop and get approval**

Present the pending events and the expected before/after table. **Do not apply
anything until the user approves.** This mutates real positions; `unapply` is the
way back, but approval comes first.

- [ ] **Step 6: Apply in ex-date order**

For each pending id, oldest ex-date first:

```bash
curl -s -X POST "http://localhost:8000/api/v1/portfolio/corporate-actions/<id>/apply"
```

Each response lists `applied_action_ids`. PAN's 2026-05-29 bonus and cash share
an ex-date and settle together, so one of the two ids will already be applied
when you reach it — skip it rather than retrying. Re-list pending between calls:

```bash
curl -s "http://localhost:8000/api/v1/portfolio/corporate-actions?status=pending" \
  | python -c "import json,sys; print([(a['id'],a['symbol'],a['ex_date']) for a in json.load(sys.stdin)])"
```

- [ ] **Step 7: Verify against the spec**

```bash
curl -s http://localhost:8000/api/v1/portfolio/summary | python -m json.tool
docker compose exec -T backend python -c "
from sqlalchemy import text
from app.db.base import engine
with engine.connect() as c:
    for r in c.execute(text('select id,ticker,quantity,purchase_price from positions order by id')):
        print(r)
"
```

Expected: quantities and prices match the `EXPECTED` table above;
`total_dividend_income_gross` is 307,044,000; `total_invested` is unchanged at
5,693,634 (in thousands); `total_value` is about 4,588,581.

If anything is off, `unapply` the events in reverse order and stop.

- [ ] **Step 8: Commit**

```bash
git add tests/test_corporate_action_backfill.py
git commit -m "test: golden backfill test for the eight stale lots"
```

---

## Self-Review

**Spec coverage.** Every section maps to a task: problem/impact → Task 10's
golden numbers; data source and the two endpoints → Task 2 (calendar
deliberately excluded per non-goals); the six-name taxonomy and prose parsing →
Task 1; accounting decisions → Tasks 3 and 7; `corporate_action` and
`corporate_action_application`, the `transactions` ENUM and the Pydantic
relaxations → Task 4; all six application semantics → Task 3 (pure) and Task 6
(persisted); ingestion and the daily poll → Tasks 5 and 9; the six endpoints and
the summary fields → Tasks 7 and 8; backfill → Task 10. Every test named in the
spec's testing section appears: parser table, same-ex-date regression,
cost-preservation-within-tolerance, eligibility, rounding, compounding order,
idempotency, unapply, golden.

**Type consistency.** `ParsedAction`/`RawEvent` (Tasks 1–2) are consumed only by
`corporate_action_service` (Task 5). `Lot`/`Event`/`Settlement` (Task 3) are
consumed only by Task 6, which maps `Settlement` onto `AppliedLot` (Task 5's
schema). `action_type` is the string `"cash"`/`"stock"` everywhere — parser,
engine, model ENUM, and schemas agree. `status` values `pending`/`applied`/
`ignored`/`unparsed` are identical in the model ENUM, the service, and the route
query parameter (which additionally accepts `"all"`, translated to `None` before
reaching `list_actions`). `_calculate_dividend_income` returns
`(gross, net)` and is used only in `get_portfolio_summary`.

**Deviations from the spec, all deliberate.**

1. The spec proposed reading dividend income as an aggregate over
   `transaction_type == 'dividend_cash'`; that yields gross only, since the
   withholding rate lives on the event. Task 7 reads the application ledger
   joined to `corporate_action` instead, giving gross and net from one query.
   The `transactions` rows remain the human-visible ledger.
2. An event with no eligible lot is marked `applied` rather than left `pending`,
   so it stops reappearing in review forever.
3. **Apply operates on the ex-date group, not one event.** The spec's rule 2
   cannot be honoured one event at a time, so `apply_action` settles every
   pending sibling sharing `(symbol, ex_date)`. This is a change to the API's
   behaviour, not just its implementation, and `ApplyResult.applied_action_ids`
   reports it.

**What this review caught.** The parser and engine code above were executed
against the real titles and the eight real lots before this plan was finalised;
both pass. Two bugs in the first draft of Task 6 were found that way and are
fixed above:

- Applying one event per call paid PAN's cash dividend on the post-bonus share
  count — 238,464,000 instead of 198,720,000, the exact error the spec's rule 2
  exists to prevent. Hence the ex-date group.
- Writing `position.quantity = s.qty_after` for every settlement reverted the
  bonus, because a cash settlement reports an unchanged quantity by design. The
  write is now guarded on `shares_added > 0`, and `unapply` is guarded the same
  way so reversing a cash dividend cannot undo a stock event that is still
  applied.

Both now have named regression tests in Task 6.

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-08-25-dividend-corporate-actions.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
