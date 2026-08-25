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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.corporate_action import CorporateAction
from app.db.models.portfolio import Position
from app.schemas.corporate_action import ManualDividendCreate
from app.services.dnse_corporate_actions import (
    ParsedAction, RawEvent, classify, fetch_history, parse_action,
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


def _store(db: Session, raw: RawEvent, action_type: str) -> ParsedAction | None:
    """Insert one event, parsed if possible and flagged if not.

    Returns what it parsed (or ``None``) so the caller can count it without
    parsing the same title a second time.
    """
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
    return parsed


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

        parsed = _store(db, raw, action_type)
        seen.add(raw.event_id)
        counts["inserted"] += 1
        if parsed is None:
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
    """A negative synthetic id, so a manual row can never collide with DNSE's.

    Advisory only: computed from an unlocked ``SELECT MIN(event_id)``, so two
    concurrent callers can compute the same value. ``create_manual`` is what
    makes the id actually safe, by retrying on the resulting unique-constraint
    collision.
    """
    lowest = db.execute(select(func.min(CorporateAction.event_id))).scalar()
    return min(0, int(lowest or 0)) - 1


_MAX_MANUAL_INSERT_ATTEMPTS = 3


def create_manual(db: Session, payload: ManualDividendCreate) -> CorporateAction:
    """Record a dividend by hand, for what the feed missed or misparsed.

    ``event_id`` is minted from an unlocked query, so two concurrent calls can
    race to the same negative id. The insert is retried a bounded number of
    times on the resulting ``IntegrityError``, recomputing the id each time,
    rather than assuming the race away.
    """
    if payload.action_type == "cash" and payload.amount_per_share is None:
        raise ValueError("amount_per_share is required for a cash dividend")
    if payload.action_type == "stock" and payload.ratio is None:
        raise ValueError("ratio is required for a stock dividend")

    tax = payload.tax_withheld_pct
    if payload.action_type == "cash" and tax is None:
        tax = DEFAULT_CASH_TAX_PCT

    last_error: IntegrityError | None = None
    for _ in range(_MAX_MANUAL_INSERT_ATTEMPTS):
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
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            last_error = exc
            continue
        db.refresh(action)
        return action

    raise last_error
