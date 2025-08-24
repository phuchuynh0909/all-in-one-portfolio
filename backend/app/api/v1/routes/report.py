from fastapi import APIRouter
from app.schemas.report import ReportResponse
from app.services.report_service import get_reports

router = APIRouter(prefix="/report", tags=["report"])

@router.get("/list", response_model=ReportResponse)
async def get_all_reports(symbol: str | None = None) -> ReportResponse:
    """Get reports, optionally filtered by symbol."""
    reports = await get_reports(symbol)
    return ReportResponse(reports=reports)
