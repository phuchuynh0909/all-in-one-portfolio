"""Account-owned chart layouts and their complete indicator setup."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_user
from app.db.base import get_db
from app.db.models.chart_layout import ChartLayout, ChartPreference
from app.db.models.openscript import OpenScript
from app.db.models.user import User
from app.schemas.chart_layout import (
    ChartLayoutImport, ChartLayoutOut, ChartLayoutsOut, ChartLayoutSelection,
    ChartLayoutName, ChartLayoutWrite, LayoutId,
)

router = APIRouter(prefix="/chart-layouts", tags=["Chart layouts"])


def _library(db, user_id):
    layouts = db.scalars(select(ChartLayout).where(ChartLayout.user_id == user_id).order_by(ChartLayout.id)).all()
    preference = db.get(ChartPreference, user_id)
    return ChartLayoutsOut(layouts=layouts, active_layout_id=preference.active_layout_id if preference else None)


def _owned_scripts(db, user_id):
    return db.scalars(select(OpenScript).where(OpenScript.user_id == user_id)).all()


def _select(db, user_id, layout_id):
    layout = db.get(ChartLayout, (user_id, layout_id))
    if layout is None:
        raise HTTPException(404, "Layout not found")
    preference = db.get(ChartPreference, user_id)
    if preference is None:
        preference = ChartPreference(user_id=user_id)
        db.add(preference)
    preference.active_layout_id = layout_id
    for script in _owned_scripts(db, user_id):
        script.visible = script.id in layout.scripts
    return layout


@router.get("", response_model=ChartLayoutsOut)
def list_layouts(db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _library(db, user.id)


@router.post("/import", response_model=ChartLayoutsOut)
def import_layouts(payload: ChartLayoutImport, db: Session = Depends(get_db), user: User = Depends(require_user)):
    existing = {layout.id: layout for layout in db.scalars(select(ChartLayout).where(ChartLayout.user_id == user.id)).all()}
    owned = {script.id for script in _owned_scripts(db, user.id)}
    names = {layout.name.casefold() for layout in existing.values()}
    for layout in payload.layouts:
        if layout.id in existing:
            continue  # Retry imports without overwriting newer server settings.
        if layout.name.casefold() in names:
            raise HTTPException(409, f'A layout named "{layout.name}" already exists')
        data = layout.model_dump()
        data["scripts"] = [script_id for script_id in layout.scripts if script_id in owned]
        row = ChartLayout(user_id=user.id, **data)
        db.add(row)
        existing[row.id] = row
        names.add(row.name.casefold())
    if payload.active_layout_id and payload.active_layout_id not in existing:
        raise HTTPException(404, "Selected layout not found in the imported library")
    db.flush()
    if payload.active_layout_id:
        _select(db, user.id, payload.active_layout_id)
    db.commit()
    return _library(db, user.id)


@router.post("/active", response_model=ChartLayoutOut)
def select_layout(payload: ChartLayoutSelection, db: Session = Depends(get_db), user: User = Depends(require_user)):
    layout = _select(db, user.id, payload.layout_id)
    db.commit()
    db.refresh(layout)
    return layout


def _custom_layout(db, user_id, layout_id):
    if not layout_id.startswith("custom_"):
        raise HTTPException(403, "Only custom layouts can be renamed or deleted")
    layout = db.get(ChartLayout, (user_id, layout_id))
    if layout is None:
        raise HTTPException(404, "Layout not found")
    return layout


@router.patch("/{layout_id}", response_model=ChartLayoutOut)
def rename_layout(layout_id: LayoutId, payload: ChartLayoutName, db: Session = Depends(get_db), user: User = Depends(require_user)):
    layout = _custom_layout(db, user.id, layout_id)
    duplicate = db.scalar(select(ChartLayout).where(
        ChartLayout.user_id == user.id, ChartLayout.id != layout_id,
        ChartLayout.name == payload.name,
    ))
    if duplicate is not None:
        raise HTTPException(409, "A layout with this name already exists")
    layout.name = payload.name
    db.commit()
    db.refresh(layout)
    return layout


@router.delete("/{layout_id}", response_model=ChartLayoutsOut)
def delete_layout(layout_id: LayoutId, db: Session = Depends(get_db), user: User = Depends(require_user)):
    layout = _custom_layout(db, user.id, layout_id)
    preference = db.get(ChartPreference, user.id)
    if preference and preference.active_layout_id == layout_id:
        if db.get(ChartLayout, (user.id, "layout1")) is not None:
            _select(db, user.id, "layout1")
        else:
            preference.active_layout_id = None
            for script in _owned_scripts(db, user.id):
                script.visible = False
    db.delete(layout)
    db.commit()
    return _library(db, user.id)


@router.put("/{layout_id}", response_model=ChartLayoutOut)
def save_layout(layout_id: LayoutId, payload: ChartLayoutWrite, db: Session = Depends(get_db), user: User = Depends(require_user)):
    duplicate = db.scalar(select(ChartLayout).where(
        ChartLayout.user_id == user.id, ChartLayout.id != layout_id,
        ChartLayout.name == payload.name,
    ))
    if duplicate is not None:
        raise HTTPException(409, "A layout with this name already exists")
    owned = {script.id for script in _owned_scripts(db, user.id)}
    if any(script_id not in owned for script_id in payload.scripts):
        raise HTTPException(404, "One or more saved scripts no longer exist in your library")
    layout = db.get(ChartLayout, (user.id, layout_id))
    if layout is None:
        layout = ChartLayout(user_id=user.id, id=layout_id)
        db.add(layout)
    for key, value in payload.model_dump().items():
        setattr(layout, key, value)
    db.commit()
    db.refresh(layout)
    return layout
