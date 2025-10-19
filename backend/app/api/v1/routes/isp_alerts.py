"""ISP Alerts API endpoints."""
from fastapi import APIRouter, Depends, Query
from typing import List, Optional
from datetime import datetime
from clickhouse_connect.driver import Client
from pydantic import BaseModel

from app.db.clickhouse import get_clickhouse_client
from app.services.isp_alerts_service import ISPAlertsService, ISPAlert


router = APIRouter()


class ISPAlertsResponse(BaseModel):
    """Response model for ISP alerts."""
    alerts: List[ISPAlert]
    total: int
    offset: int
    limit: int


def get_isp_service(
    clickhouse_client: Client = Depends(get_clickhouse_client)
) -> ISPAlertsService:
    """
    Dependency to get ISP Alerts service.
    
    Args:
        clickhouse_client: ClickHouse client from dependency
        
    Returns:
        ISPAlertsService instance
    """
    return ISPAlertsService(clickhouse_client)


@router.get("/alerts", response_model=ISPAlertsResponse)
async def get_isp_alerts(
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    limit: int = Query(100, ge=1, le=1000, description="Number of records to return"),
    symbol: Optional[str] = Query(None, description="Filter by symbol"),
    min_abnormality: Optional[float] = Query(None, description="Minimum abnormality ratio"),
    since: Optional[datetime] = Query(None, description="Get alerts since this timestamp"),
    service: ISPAlertsService = Depends(get_isp_service),
):
    """
    Get ISP alerts from ClickHouse.
    
    Args:
        offset: Number of records to skip
        limit: Maximum number of records to return
        symbol: Filter by specific symbol
        min_abnormality: Filter by minimum abnormality ratio (any window)
        since: Get only alerts after this timestamp
        service: ISP alerts service (injected)
    
    Returns:
        ISPAlertsResponse with alerts and metadata
    """
    alerts, total = service.get_alerts(
        offset=offset,
        limit=limit,
        symbol=symbol,
        min_abnormality=min_abnormality,
        since=since,
    )
    
    return ISPAlertsResponse(
        alerts=alerts,
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/alerts/latest", response_model=List[ISPAlert])
async def get_latest_alerts(
    limit: int = Query(50, ge=1, le=5000, description="Number of latest alerts"),
    since: Optional[int] = Query(None, description="Get alerts since this Unix timestamp in milliseconds"),
    service: ISPAlertsService = Depends(get_isp_service),
):
    """
    Get latest alerts since a specific timestamp.
    
    This is optimized for real-time display with incremental loading.
    
    Args:
        limit: Maximum number of alerts to return
        since: Unix timestamp in milliseconds. If not provided, returns most recent alerts.
        service: ISP alerts service (injected)
    
    Returns:
        List of latest ISP alerts ordered by timestamp DESC
    """
    # Convert Unix timestamp (ms) to datetime if provided
    since_dt = datetime.fromtimestamp(since / 1000) if since else None
    return service.get_latest_alerts(limit=limit, since=since_dt)


@router.get("/alerts/symbols", response_model=List[str])
async def get_active_symbols(
    seconds: int = Query(300, ge=1, le=3600, description="Get symbols active in last N seconds"),
    service: ISPAlertsService = Depends(get_isp_service),
):
    """
    Get list of symbols that have recent alerts.
    
    Args:
        seconds: Look back this many seconds
        service: ISP alerts service (injected)
    
    Returns:
        List of active symbol names
    """
    return service.get_active_symbols(seconds=seconds)


@router.get("/alerts/statistics", response_model=dict)
async def get_alert_statistics(
    seconds: int = Query(300, ge=1, le=3600, description="Get statistics from last N seconds"),
    service: ISPAlertsService = Depends(get_isp_service),
):
    """
    Get statistics about recent alerts.
    
    Args:
        seconds: Look back this many seconds
        service: ISP alerts service (injected)
    
    Returns:
        Statistics dictionary with averages and maximums
    """
    return service.get_alert_statistics(seconds=seconds)

