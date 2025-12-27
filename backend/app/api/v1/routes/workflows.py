from fastapi import APIRouter
from pydantic import BaseModel
from loguru import logger
from app.services.prefect_workflow_service import run_sync_stock_workflow


router = APIRouter(prefix="/workflows", tags=["workflows"])


class TriggerResponse(BaseModel):
    started: bool
    detail: str

@router.post("/sync-stock/{symbol}", response_model=TriggerResponse, status_code=202)
async def trigger_feature_store(symbol: str) -> TriggerResponse:
    """Trigger the Prefect flow that builds and syncs the feature store.

    Runs in background so the API returns immediately.
    """
    # background_tasks.add_task(_run_feature_store_flow)
    flow_run = await run_sync_stock_workflow(symbol)
    # Convert State object to string for response
    state_str = str(flow_run.state) if flow_run.state else "Unknown"
    return TriggerResponse(started=True, detail=state_str)


