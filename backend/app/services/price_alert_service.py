"""Service for managing price alerts."""
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.db.models.portfolio import PriceAlert as PriceAlertModel
from app.schemas.price_alert import (
    PriceAlertCreate,
    PriceAlertUpdate,
    PriceAlert,
    PriceAlertWithPrice,
    AlertCondition,
)


def create_alert(db: Session, alert: PriceAlertCreate) -> PriceAlert:
    """Create a new price alert."""
    db_alert = PriceAlertModel(
        symbol=alert.symbol.upper(),
        condition=alert.condition.value,
        target_price=alert.target_price,
        notes=alert.notes,
    )
    db.add(db_alert)
    db.commit()
    db.refresh(db_alert)
    return _model_to_schema(db_alert)


def get_alert(db: Session, alert_id: int) -> Optional[PriceAlert]:
    """Get a price alert by ID."""
    db_alert = db.query(PriceAlertModel).filter(PriceAlertModel.id == alert_id).first()
    if db_alert:
        return _model_to_schema(db_alert)
    return None


def get_alerts(
    db: Session,
    symbol: Optional[str] = None,
    is_active: Optional[bool] = None,
    is_triggered: Optional[bool] = None,
    offset: int = 0,
    limit: int = 100,
) -> Tuple[List[PriceAlert], int]:
    """Get price alerts with optional filtering."""
    query = db.query(PriceAlertModel)
    
    # Apply filters
    filters = []
    if symbol:
        filters.append(PriceAlertModel.symbol == symbol.upper())
    if is_active is not None:
        filters.append(PriceAlertModel.is_active == (1 if is_active else 0))
    if is_triggered is not None:
        filters.append(PriceAlertModel.is_triggered == (1 if is_triggered else 0))
    
    if filters:
        query = query.filter(and_(*filters))
    
    # Get total count
    total = query.count()
    
    # Apply pagination and ordering
    alerts = query.order_by(PriceAlertModel.created_at.desc()).offset(offset).limit(limit).all()
    
    return [_model_to_schema(a) for a in alerts], total


def update_alert(db: Session, alert_id: int, update: PriceAlertUpdate) -> Optional[PriceAlert]:
    """Update a price alert."""
    db_alert = db.query(PriceAlertModel).filter(PriceAlertModel.id == alert_id).first()
    if not db_alert:
        return None
    
    update_data = update.model_dump(exclude_unset=True)
    
    for field, value in update_data.items():
        if field == "condition" and value is not None:
            setattr(db_alert, field, value.value if isinstance(value, AlertCondition) else value)
        elif field == "is_active" and value is not None:
            setattr(db_alert, field, 1 if value else 0)
        elif value is not None:
            setattr(db_alert, field, value)
    
    db_alert.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(db_alert)
    return _model_to_schema(db_alert)


def delete_alert(db: Session, alert_id: int) -> bool:
    """Delete a price alert."""
    db_alert = db.query(PriceAlertModel).filter(PriceAlertModel.id == alert_id).first()
    if not db_alert:
        return False
    
    db.delete(db_alert)
    db.commit()
    return True


def toggle_alert_active(db: Session, alert_id: int) -> Optional[PriceAlert]:
    """Toggle the active status of a price alert."""
    db_alert = db.query(PriceAlertModel).filter(PriceAlertModel.id == alert_id).first()
    if not db_alert:
        return None
    
    db_alert.is_active = 0 if db_alert.is_active == 1 else 1
    db_alert.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(db_alert)
    return _model_to_schema(db_alert)


def mark_alert_triggered(db: Session, alert_id: int) -> Optional[PriceAlert]:
    """Mark a price alert as triggered."""
    db_alert = db.query(PriceAlertModel).filter(PriceAlertModel.id == alert_id).first()
    if not db_alert:
        return None
    
    db_alert.is_triggered = 1
    db_alert.triggered_at = datetime.utcnow()
    db_alert.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(db_alert)
    return _model_to_schema(db_alert)


def reset_alert(db: Session, alert_id: int) -> Optional[PriceAlert]:
    """Reset a triggered alert to active state."""
    db_alert = db.query(PriceAlertModel).filter(PriceAlertModel.id == alert_id).first()
    if not db_alert:
        return None
    
    db_alert.is_triggered = 0
    db_alert.triggered_at = None
    db_alert.is_active = 1
    db_alert.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(db_alert)
    return _model_to_schema(db_alert)


def _model_to_schema(db_alert: PriceAlertModel) -> PriceAlert:
    """Convert database model to Pydantic schema."""
    return PriceAlert(
        id=db_alert.id,
        symbol=db_alert.symbol,
        condition=AlertCondition(db_alert.condition),
        target_price=db_alert.target_price,
        is_active=db_alert.is_active == 1,
        is_triggered=db_alert.is_triggered == 1,
        triggered_at=db_alert.triggered_at,
        notes=db_alert.notes,
        created_at=db_alert.created_at,
        updated_at=db_alert.updated_at,
    )


def enrich_alert_with_price(alert: PriceAlert, current_price: Optional[float]) -> PriceAlertWithPrice:
    """Enrich a price alert with current price information."""
    price_diff = None
    price_diff_pct = None
    
    if current_price is not None and alert.target_price:
        # Convert Decimal to float for arithmetic
        target_price_float = float(alert.target_price)
        price_diff = current_price - target_price_float
        if target_price_float != 0:
            price_diff_pct = (price_diff / target_price_float) * 100
    
    return PriceAlertWithPrice(
        **alert.model_dump(),
        current_price=current_price,
        price_diff=price_diff,
        price_diff_pct=price_diff_pct,
    )

