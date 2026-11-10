from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi_cache.decorator import cache
from loguru import logger
from sqlalchemy.orm import Session
from app.db.base import get_db
from app.services import rag_submissions
from app.schemas.report import Report, ReportCreate, ReportResponse, ReportDetail, ReportSummaryUpdate
from app.services.report_service import (
    create_report,
    get_reports,
    get_report_by_id,
    update_report_summary,
    sync_latest_reports,
)
from app.stores.raw_wichart_report import ReportIdTaken

router = APIRouter(prefix="/report", tags=["report"])


@router.get("/list", response_model=ReportResponse)
@cache(expire=3600)  # Cache for 1 hour
def get_all_reports(symbol: str | None = None) -> ReportResponse:
    """Get reports, optionally filtered by symbol."""
    reports = get_reports(symbol)
    return ReportResponse(reports=reports)


@router.post("", response_model=Report, status_code=201)
def add_report(payload: ReportCreate) -> Report:
    """Add a report by hand (for what the crawler missed).

    Lands in the same feed table as crawled reports, so the new row is
    immediately usable by the detail page and the RAG pipeline. Send ``id`` to
    pin the feed id (409 if it is already used), or omit it to have one
    allocated. ``GET /report/list`` is cached for an hour — the client should
    refetch it with ``Cache-Control: no-cache`` to see the new report right away.
    """
    try:
        return create_report(payload)
    except ReportIdTaken as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to add manual report")
        raise HTTPException(status_code=500, detail="Failed to add report") from exc


@router.get("/{report_id}", response_model=ReportDetail)
def get_report(report_id: int) -> ReportDetail:
    """Get a single report by ID."""
    report = get_report_by_id(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.put("/{report_id}/summary")
def save_report_summary(report_id: int, data: ReportSummaryUpdate):
    """Update llm_summary field in wichart_reports table."""
    success = update_report_summary(report_id, data.summary)
    if not success:
        raise HTTPException(status_code=404, detail="Report not found")
    return {"message": "Summary saved successfully"}


@router.get("/rag/statuses")
def list_rag_statuses(db: Session = Depends(get_db)):
    """Bulk RAG status for the Report list (which reports are embedded)."""
    from app.services import report_rag_service as rag_service

    return {"statuses": rag_submissions.pending_statuses(db, rag_service.list_statuses())}


@router.get("/rag/health")
def rag_health():
    """Report the status store the API reads from + row count.

    Compare ``store`` here with the worker's "report_rag status store (worker)"
    log line: if they differ, the job writes status to a different database than
    the API reads, which looks like "status not updating".
    """
    from app.services import report_rag_service as rag_service

    rows = rag_service.list_statuses()
    return {"store": rag_service.endpoint(), "tracked_reports": len(rows)}


@router.post("/{report_id}/rag", status_code=202)
def trigger_rag(
    report_id: int,
    recreate: bool = False,
    parser: str | None = None,
    idempotency_key: UUID | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    """Queue the RAG pipeline (PDF -> markdown -> embeddings -> Qdrant) for a report.

    ``parser`` (one of ``report_rag_service.PDF_PARSERS``) overrides the server
    default.
    """
    from app.services import report_rag_service as rag_service

    if parser is not None and parser not in rag_service.PDF_PARSERS:
        raise HTTPException(
            status_code=400,
            detail=f"parser must be one of: {', '.join(rag_service.PDF_PARSERS)}",
        )

    try:
        submission = rag_submissions.enqueue(db, idempotency_key or uuid4(), report_id, recreate, parser)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"report_id": report_id, "status": rag_service.PENDING, "submission_id": submission.id}


@router.get("/{report_id}/rag")
def get_rag_status(report_id: int, db: Session = Depends(get_db)):
    """Current RAG status for one report."""
    from app.services import report_rag_service as rag_service

    status = rag_service.get_status(report_id)
    rows = rag_submissions.pending_statuses(db, [status] if status else [])
    return next((row for row in rows if row["report_id"] == report_id), {"report_id": report_id, "status": "NONE"})


@router.get("/{report_id}/markdown")
def get_report_markdown(report_id: int):
    """Parsed markdown for a report (available after the parse step)."""
    from app.services import report_rag_service as rag_service

    md = rag_service.get_markdown(report_id)
    if md is None:
        raise HTTPException(status_code=404, detail="Markdown not available; run the RAG pipeline first")
    return {"report_id": report_id, "markdown": md}


@router.post("/sync")
def sync_reports(limit: int = 100):
    """
    Sync latest reports from raw_wichart_report to wichart_reports.
    Finds missing records and automatically initializes them.
    """
    stats = sync_latest_reports(limit=limit)
    return {
        "message": "Sync completed",
        "stats": stats,
    }
