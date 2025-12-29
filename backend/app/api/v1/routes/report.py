from fastapi import APIRouter, HTTPException
from fastapi_cache.decorator import cache
from app.schemas.report import ReportResponse, ReportDetail, ReportSummaryUpdate
from app.services.report_service import get_reports, get_report_by_id, update_report_summary, sync_latest_reports

router = APIRouter(prefix="/report", tags=["report"])


@router.get("/list", response_model=ReportResponse)
@cache(expire=3600)  # Cache for 1 hour
async def get_all_reports(symbol: str | None = None) -> ReportResponse:
    """Get reports, optionally filtered by symbol."""
    reports = await get_reports(symbol)
    return ReportResponse(reports=reports)


@router.get("/{report_id}", response_model=ReportDetail)
async def get_report(report_id: int) -> ReportDetail:
    """Get a single report by ID."""
    report = await get_report_by_id(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.put("/{report_id}/summary")
async def save_report_summary(report_id: int, data: ReportSummaryUpdate):
    """Update llm_summary field in wichart_reports table."""
    success = await update_report_summary(report_id, data.summary)
    if not success:
        raise HTTPException(status_code=404, detail="Report not found")
    return {"message": "Summary saved successfully"}


@router.post("/sync")
async def sync_reports(limit: int = 100):
    """
    Sync latest reports from raw_wichart_report to wichart_reports.
    Finds missing records and automatically initializes them.
    """
    stats = await sync_latest_reports(limit=limit)
    return {
        "message": "Sync completed",
        "stats": stats,
    }
