"""Account-owned watchlists, their ordered contents, and active selection."""
import hashlib

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import require_user
from app.db.base import get_db
from app.db.models.watchlist import WatchlistLibrary
from app.db.models.user import User
from app.schemas.watchlist import WatchlistLibraryOut, WatchlistWrite

router = APIRouter(prefix="/watchlists", tags=["Watchlists"])


def _out(row):
    if row is None:
        return WatchlistLibraryOut()
    return WatchlistLibraryOut(lists=row.lists, active_list_id=row.active_list_id, imported_ids=row.imported_ids)


@router.get("", response_model=WatchlistLibraryOut)
def get_watchlists(db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _out(db.get(WatchlistLibrary, user.id))


@router.post("/initialize", response_model=WatchlistLibraryOut)
def initialize(payload: WatchlistWrite, db: Session = Depends(get_db), user: User = Depends(require_user)):
    # Lock the parent row so simultaneous first visits cannot create duplicates.
    db.query(User).filter(User.id == user.id).with_for_update().one()
    row = db.get(WatchlistLibrary, user.id)
    if row is None:
        row = WatchlistLibrary(user_id=user.id, **payload.model_dump(), imported_ids=[])
        db.add(row)
        db.commit()
        db.refresh(row)
    return _out(row)


@router.put("", response_model=WatchlistLibraryOut)
def save_watchlists(payload: WatchlistWrite, db: Session = Depends(get_db), user: User = Depends(require_user)):
    db.query(User).filter(User.id == user.id).with_for_update().one()
    row = db.get(WatchlistLibrary, user.id)
    if row is None:
        row = WatchlistLibrary(user_id=user.id, imported_ids=[])
        db.add(row)
    row.lists = [item.model_dump() for item in payload.lists]
    row.active_list_id = payload.active_list_id
    db.commit()
    db.refresh(row)
    return _out(row)


@router.post("/import", response_model=WatchlistLibraryOut)
def import_watchlists(payload: WatchlistWrite, db: Session = Depends(get_db), user: User = Depends(require_user)):
    db.query(User).filter(User.id == user.id).with_for_update().one()
    row = db.get(WatchlistLibrary, user.id)
    lists = {item["id"]: dict(item) for item in row.lists} if row else {}
    imported = set(row.imported_ids if row else [])
    selection = {}
    for incoming in payload.lists:
        item = incoming.model_dump()
        target_id = incoming.id
        if target_id in lists and lists[target_id] != item:
            target_id = "browser_" + hashlib.sha256(incoming.id.encode()).hexdigest()[:24]
            item["id"] = target_id
            item["title"] = f"{incoming.title[:69]} (imported)"
        selection[incoming.id] = target_id
        # Keep newer server edits and avoid resurrecting a previously imported list.
        if incoming.id not in imported and target_id not in lists:
            lists[target_id] = item
        imported.add(incoming.id)
    if len(lists) > 100:
        raise HTTPException(422, "Keep at most 100 watchlists")
    active = selection[payload.active_list_id]
    if active not in lists:
        active = row.active_list_id if row else next(iter(lists))
    if row is None:
        row = WatchlistLibrary(user_id=user.id)
        db.add(row)
    row.lists = list(lists.values())
    row.active_list_id = active
    row.imported_ids = sorted(imported)
    db.commit()
    db.refresh(row)
    return _out(row)
