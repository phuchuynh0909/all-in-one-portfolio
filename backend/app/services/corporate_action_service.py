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

from app.db.models.corporate_action import CorporateAction, CorporateActionApplication
from app.db.models.portfolio import Position, Transaction
from app.schemas.corporate_action import AppliedLot, ApplyResult, ManualDividendCreate
from app.services.corporate_action_engine import Event, Lot, settle
from app.services.dnse_corporate_actions import (
    ParsedAction, RawEvent, classify, fetch_history, parse_action,
)

# Vietnam withholds 5% PIT on cash dividends. Stored as a rate so the net
# figure can change without a migration; gross stays the factual record.
DEFAULT_CASH_TAX_PCT = Decimal("0.05")

# DNSE reports cash dividends in VND per share ("800 đồng/CP") while every
# money column in positions/transactions is in thousands of VND
# (purchase_price 24.10 == 24,100 VND). Convert once, here, so the ledger,
# the transaction rows and the portfolio summary all speak the same units.
#
# ``corporate_action.amount_per_share`` deliberately stays in raw VND: it is the
# audit record of what the title literally said. This constant is the single
# boundary where that figure enters the domain's own units.
VND_PER_PRICE_UNIT = Decimal(1000)


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


def _assert_ex_date_order(db: Session, action: CorporateAction) -> None:
    """Refuse to apply an event out of ex-date order (spec rule 5).

    ``apply_action`` hands the engine a single ex-date group, so the engine's
    own ordering logic never runs in production — the *caller* decides what is
    applied when, and ``POST /{id}/apply`` will accept any id in any order.
    Nothing else stops the three silent money errors that follow:

    * VCG lot 4's 2026-07-14 cash applied before its 2025-06-11 stock event pays
      on 40,820 shares instead of 44,085 — 2,612,000 VND understated — while the
      final quantity still lands correctly, so nothing looks wrong afterwards.
    * PAN's cash re-applied after an unapply, with the same-day bonus still
      applied, settles on 79,488 instead of 66,240.
    * A sibling left ``unparsed`` and corrected manually later is this same path.

    Neither message may contain "not found": the HTTP layer maps that substring
    to 404, and an ordering violation must surface as 409.
    """
    earlier_pending = db.execute(
        select(CorporateAction)
        .where(
            CorporateAction.symbol == action.symbol,
            CorporateAction.status == "pending",
            CorporateAction.ex_date < action.ex_date,
        )
        .order_by(CorporateAction.ex_date, CorporateAction.id)
    ).scalars().first()
    if earlier_pending is not None:
        raise ValueError(
            f"Corporate action {action.id} ({action.symbol}, ex-date "
            f"{action.ex_date}) cannot be applied yet: the earlier event "
            f"{earlier_pending.id} with ex-date {earlier_pending.ex_date} is "
            "still pending. Apply that one first — events must be applied "
            "strictly ascending by ex-date."
        )

    later_applied = db.execute(
        select(CorporateAction)
        .where(
            CorporateAction.symbol == action.symbol,
            CorporateAction.status == "applied",
            CorporateAction.ex_date >= action.ex_date,
        )
        .order_by(CorporateAction.ex_date.desc(), CorporateAction.id.desc())
    ).scalars().first()
    if later_applied is not None:
        raise ValueError(
            f"Corporate action {action.id} ({action.symbol}, ex-date "
            f"{action.ex_date}) cannot be applied: event {later_applied.id} "
            f"with ex-date {later_applied.ex_date} is already applied and is "
            "not earlier. Unapply that one first — events must be applied "
            "strictly ascending by ex-date."
        )


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
    _assert_ex_date_order(db, action)

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
                # The engine is unit-agnostic and settles in whatever units the
                # event carried, i.e. raw VND. Everything written below is in
                # price units, so the conversion happens exactly here.
                cash_amount = (
                    s.cash_amount / VND_PER_PRICE_UNIT
                    if s.cash_amount is not None else None
                )
                transaction = Transaction(
                    ticker=action.symbol,
                    transaction_type=(
                        "dividend_cash" if s.action_type == "cash" else "dividend_stock"
                    ),
                    quantity=(
                        s.qty_before if s.action_type == "cash" else s.shares_added
                    ),
                    price=(
                        source.amount_per_share / VND_PER_PRICE_UNIT
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
                    cash_amount=cash_amount,
                ))
                applied.append(AppliedLot(
                    position_id=position.id,
                    qty_before=s.qty_before, qty_after=s.qty_after,
                    price_before=s.price_before, price_after=s.price_after,
                    shares_added=s.shares_added, cash_amount=cash_amount,
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
