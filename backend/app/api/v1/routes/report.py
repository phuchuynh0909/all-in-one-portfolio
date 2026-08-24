import os
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi_cache.decorator import cache
from loguru import logger
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


def _run_rag_pipeline(report_id: int, recreate: bool, parser: str | None) -> None:
    """Background worker: run the report RAG pipeline for one report.

    With ``RAG_USE_DEPLOYMENT`` set, dispatches to the Prefect deployment (heavy
    work runs on the ``my-worker`` pool — register it with
    ``python tasks/rag_pipeline.py --deploy``). Otherwise runs the flow in-process.
    Imported lazily so the heavy RAG stack is only loaded when a job is triggered.
    The flow records FAILED status itself on error; we just log here.
    """
    use_deployment = True #os.getenv("RAG_USE_DEPLOYMENT", "0").lower() in ("1", "true", "yes", "on")
    try:
        if use_deployment:
            from app.services.prefect_workflow_service import run_rag_pipeline_deployment

            run_rag_pipeline_deployment(report_id, recreate, parser)
        else:
            from tasks.rag_pipeline import rag_pipeline_flow

            rag_pipeline_flow(report_id, recreate=recreate, parser=parser)
    except Exception:  # noqa: BLE001
        logger.exception("RAG pipeline background task failed for report {}", report_id)


@router.get("/list", response_model=ReportResponse)
@cache(expire=3600)  # Cache for 1 hour
async def get_all_reports(symbol: str | None = None) -> ReportResponse:
    """Get reports, optionally filtered by symbol."""
    reports = await get_reports(symbol)
    return ReportResponse(reports=reports)


@router.post("", response_model=Report, status_code=201)
async def add_report(payload: ReportCreate) -> Report:
    """Add a report by hand (for what the crawler missed).

    Lands in the same feed table as crawled reports, so the new row is
    immediately usable by the detail page and the RAG pipeline. Send ``id`` to
    pin the feed id (409 if it is already used), or omit it to have one
    allocated. ``GET /report/list`` is cached for an hour — the client should
    refetch it with ``Cache-Control: no-cache`` to see the new report right away.
    """
    try:
        return await create_report(payload)
    except ReportIdTaken as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to add manual report")
        raise HTTPException(status_code=500, detail=f"Failed to add report: {exc}")


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


@router.get("/rag/statuses")
async def list_rag_statuses():
    """Bulk RAG status for the Report list (which reports are embedded)."""
    from app.services import report_rag_service as rag_service

    return {"statuses": rag_service.list_statuses()}


@router.get("/rag/health")
async def rag_health():
    """Report the status store the API reads from + row count.

    Compare ``store`` here with the worker's "report_rag status store (worker)"
    log line: if they differ, the job writes status to a different database than
    the API reads, which looks like "status not updating".
    """
    from app.services import report_rag_service as rag_service

    rows = rag_service.list_statuses()
    return {"store": rag_service.endpoint(), "tracked_reports": len(rows)}


@router.post("/{report_id}/rag", status_code=202)
async def trigger_rag(
    report_id: int,
    background_tasks: BackgroundTasks,
    recreate: bool = False,
    parser: str | None = None,
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

    # Seed the row with report metadata (not just status). An empty PENDING
    # INSERT followed by worker INSERT+lightweight UPDATE was leaving FINAL
    # rows with blank symbol/title/pdf_url.
    from app.services.report_service import _none_if_nan, _query_raw_reports

    meta = {"symbol": "", "title": "", "pdf_url": ""}
    df = _query_raw_reports(report_id=report_id)
    if df is not None and not df.empty:
        row = df.iloc[0]
        meta = {
            "symbol": str(_none_if_nan(row.get("mack")) or "").upper(),
            "title": str(_none_if_nan(row.get("tenbaocao")) or ""),
            "pdf_url": str(_none_if_nan(row.get("url")) or ""),
        }

    rag_service.save(
        report_id,
        symbol=meta["symbol"],
        title=meta["title"],
        pdf_url=meta["pdf_url"],
        status=rag_service.PENDING,
        error="",
    )
    background_tasks.add_task(_run_rag_pipeline, report_id, recreate, parser)
    return {"report_id": report_id, "status": rag_service.PENDING, "message": "RAG pipeline queued"}


@router.get("/{report_id}/rag")
async def get_rag_status(report_id: int):
    """Current RAG status for one report."""
    from app.services import report_rag_service as rag_service

    status = rag_service.get_status(report_id)
    return status or {"report_id": report_id, "status": "NONE"}


@router.get("/{report_id}/markdown")
async def get_report_markdown(report_id: int):
    """Parsed markdown for a report (available after the parse step)."""
    from app.services import report_rag_service as rag_service

    md = rag_service.get_markdown(report_id)
    if md is None:
        raise HTTPException(status_code=404, detail="Markdown not available; run the RAG pipeline first")
    return {"report_id": report_id, "markdown": md}


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
