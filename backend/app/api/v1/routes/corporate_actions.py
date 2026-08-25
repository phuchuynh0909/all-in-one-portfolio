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
