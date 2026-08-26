"""Trade-flow anomaly API — Isolation Forest over windowed features."""
import os
from datetime import date, timedelta
from typing import Optional

from clickhouse_connect.driver import Client
from fastapi import APIRouter, Depends, Query

from app.db.clickhouse import get_clickhouse_client
from app.services.trade_flow_service import TradeFlowResponse, TradeFlowService

router = APIRouter()


def get_trade_flow_service(
    clickhouse_client: Client = Depends(get_clickhouse_client),
) -> TradeFlowService:
    """Build the scorer with the same knobs the worker's feature view uses.

    `BLOCK_EP_WINDOW_SECONDS` must match what
    `block_episode_ingest.py --setup` built the view with — it only labels the
    response and scales the forward-return horizons, but a mismatch makes both
    misleading.
    """
    return TradeFlowService(
        clickhouse_client,
        window_seconds=int(os.getenv("BLOCK_EP_WINDOW_SECONDS", "60")),
        tod_bucket_minutes=int(os.getenv("BLOCK_EP_TOD_BUCKET_MINUTES", "30")),
        min_windows_to_fit=int(os.getenv("BLOCK_EP_MIN_WINDOWS_TO_FIT", "400")),
        contamination=float(os.getenv("BLOCK_EP_CONTAMINATION", "0.03")),
    )


@router.get(
    "/trade-flow/anomalies",
    response_model=TradeFlowResponse,
    tags=["Trade Flow"],
)
async def get_trade_flow_anomalies(
    symbol: str = Query(..., description="Symbol, e.g. HPG"),
    from_date: Optional[str] = Query(
        None, description="Start date YYYY-MM-DD (default: 7 days ago)"
    ),
    to_date: Optional[str] = Query(
        None, description="End date YYYY-MM-DD (default: today)"
    ),
    limit: int = Query(500, ge=1, le=5000, description="Max windows returned"),
    only_flagged: bool = Query(
        True,
        description="Return only windows Isolation Forest flagged as unusual",
    ),
    service: TradeFlowService = Depends(get_trade_flow_service),
):
    """Unusual trade-flow windows for a symbol.

    Isolation Forest flags a window whose whole feature vector is unusual for
    this symbol at this time of day. It is point-in-time only — it does not
    distinguish a lone odd window from the start of a sustained run.

    Features are normalized per symbol and 30-minute time-of-day bucket using
    median/MAD, so 09:15 is not compared against 13:45 and an illiquid ticker is
    not compared against HPG.

    Caveat: this tape has no order book, so a flagged window is evidence of
    unusual *executed* flow — not proof of an institution, and "absorption"
    cannot attribute which side absorbed which. Forward returns are included for
    validation and are approximate across session gaps.

    Needs the feature view: `python workers/block_episode_ingest.py --setup`.
    """
    to_day = date.fromisoformat(to_date) if to_date else date.today()
    from_day = (
        date.fromisoformat(from_date) if from_date else to_day - timedelta(days=7)
    )
    return service.get_anomalies(
        symbol=symbol.strip().upper(),
        from_day=from_day,
        to_day=to_day,
        limit=limit,
        only_flagged=only_flagged,
    )
