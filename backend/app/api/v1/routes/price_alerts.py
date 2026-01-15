"""API routes for price alerts."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.schemas.price_alert import (
    PriceAlert,
    PriceAlertCreate,
    PriceAlertUpdate,
    PriceAlertsResponse,
    PriceAlertWithPrice,
)
from app.services import price_alert_service
from app.services.stock_service import get_current_price

router = APIRouter(prefix="/price-alerts", tags=["Price Alerts"])


@router.post("", response_model=PriceAlert)
def create_alert(
    alert: PriceAlertCreate,
    db: Session = Depends(get_db),
) -> PriceAlert:
    """Create a new price alert."""
    return price_alert_service.create_alert(db, alert)


@router.get("", response_model=PriceAlertsResponse)
async def list_alerts(
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    is_triggered: Optional[bool] = Query(None, description="Filter by triggered status"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(100, ge=1, le=500, description="Pagination limit"),
    db: Session = Depends(get_db),
) -> PriceAlertsResponse:
    """List price alerts with optional filtering."""
    alerts, total = price_alert_service.get_alerts(
        db, symbol=symbol, is_active=is_active, is_triggered=is_triggered, offset=offset, limit=limit
    )
    
    # Enrich alerts with current prices
    enriched_alerts = []
    for alert in alerts:
        try:
            current_price = await get_current_price(alert.symbol)
        except Exception:
            current_price = None
        enriched_alerts.append(
            price_alert_service.enrich_alert_with_price(alert, current_price)
        )
    
    return PriceAlertsResponse(alerts=enriched_alerts, total=total)


@router.get("/{alert_id}", response_model=PriceAlertWithPrice)
async def get_alert(
    alert_id: int,
    db: Session = Depends(get_db),
) -> PriceAlertWithPrice:
    """Get a price alert by ID."""
    alert = price_alert_service.get_alert(db, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    
    try:
        current_price = await get_current_price(alert.symbol)
    except Exception:
        current_price = None
    
    return price_alert_service.enrich_alert_with_price(alert, current_price)


@router.put("/{alert_id}", response_model=PriceAlert)
def update_alert(
    alert_id: int,
    update: PriceAlertUpdate,
    db: Session = Depends(get_db),
) -> PriceAlert:
    """Update a price alert."""
    alert = price_alert_service.update_alert(db, alert_id, update)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


@router.delete("/{alert_id}")
def delete_alert(
    alert_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """Delete a price alert."""
    if not price_alert_service.delete_alert(db, alert_id):
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"status": "success", "message": "Alert deleted"}


@router.post("/{alert_id}/toggle", response_model=PriceAlert)
def toggle_alert(
    alert_id: int,
    db: Session = Depends(get_db),
) -> PriceAlert:
    """Toggle the active status of a price alert."""
    alert = price_alert_service.toggle_alert_active(db, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


@router.post("/{alert_id}/reset", response_model=PriceAlert)
def reset_alert(
    alert_id: int,
    db: Session = Depends(get_db),
) -> PriceAlert:
    """Reset a triggered alert to active state."""
    alert = price_alert_service.reset_alert(db, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert

