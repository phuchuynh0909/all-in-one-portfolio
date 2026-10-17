"""Account-owned OpenScript sources. Compilation/execution stays in the browser."""
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_user
from app.db.base import get_db
from app.db.models.openscript import OpenScript
from app.db.models.user import User
from app.schemas.openscript import OpenScriptOut, OpenScriptVisibility, OpenScriptWrite, ScriptId

router = APIRouter(prefix="/openscripts", tags=["OpenScript"])


@router.get("", response_model=list[OpenScriptOut])
def list_scripts(db: Session = Depends(get_db), user: User = Depends(require_user)):
    return db.scalars(select(OpenScript).where(OpenScript.user_id == user.id).order_by(OpenScript.title, OpenScript.id)).all()


@router.patch("/visibility", status_code=204)
def set_visibility(
    changes: Annotated[list[OpenScriptVisibility], Body(max_length=500)],
    db: Session = Depends(get_db), user: User = Depends(require_user),
):
    rows = {row.id: row for row in db.scalars(select(OpenScript).where(
        OpenScript.user_id == user.id, OpenScript.id.in_([change.id for change in changes]),
    )).all()}
    if any(change.id not in rows for change in changes):
        raise HTTPException(404, "Saved script not found")
    for change in changes:
        rows[change.id].visible = change.visible
    db.commit()
    return Response(status_code=204)


@router.post("/import", response_model=list[OpenScriptOut])
def import_scripts(
    scripts: Annotated[list[OpenScriptOut], Body(max_length=500)],
    db: Session = Depends(get_db), user: User = Depends(require_user),
):
    # Import is retryable and preserves server edits when an ID already exists.
    existing = {row.id: row for row in db.scalars(select(OpenScript).where(OpenScript.user_id == user.id)).all()}
    for script in scripts:
        if script.id not in existing:
            row = OpenScript(user_id=user.id, **script.model_dump())
            db.add(row)
            existing[script.id] = row
    db.commit()
    return list_scripts(db, user)


@router.put("/{script_id}", response_model=OpenScriptOut)
def save_script(
    script_id: ScriptId, payload: OpenScriptWrite,
    db: Session = Depends(get_db), user: User = Depends(require_user),
):
    row = db.get(OpenScript, (user.id, script_id))
    if row is None:
        row = OpenScript(user_id=user.id, id=script_id)
        db.add(row)
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{script_id}", status_code=204)
def delete_script(script_id: ScriptId, db: Session = Depends(get_db), user: User = Depends(require_user)):
    row = db.get(OpenScript, (user.id, script_id))
    if row is None:
        raise HTTPException(404, "Saved script not found")
    db.delete(row)
    db.commit()
    return Response(status_code=204)
