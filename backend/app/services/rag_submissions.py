"""Durable, idempotent handoff from the API to Prefect."""
from datetime import datetime, timedelta
from uuid import UUID

from loguru import logger
from prefect.exceptions import ObjectNotFound
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.rag_submission import RagSubmission
from app.services.prefect_workflow_service import get_rag_flow_run, run_rag_pipeline_deployment


def enqueue(db: Session, key: UUID, report_id: int, recreate: bool, parser: str | None) -> RagSubmission:
    existing = db.get(RagSubmission, str(key))
    if existing is not None:
        if (existing.report_id, existing.recreate, existing.parser) != (report_id, recreate, parser):
            raise ValueError("Idempotency key was already used for another request")
        return existing
    now = datetime.now()
    submission = RagSubmission(id=str(key), report_id=report_id, active_report_id=report_id, recreate=recreate, parser=parser,
                               state="queued", created_at=now, next_attempt_at=now)
    db.add(submission)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.get(RagSubmission, str(key))
        if existing is None:
            if db.query(RagSubmission).filter(RagSubmission.active_report_id == report_id).first():
                raise ValueError("A RAG job for this report is already queued or running")
            raise
        if (existing.report_id, existing.recreate, existing.parser) != (report_id, recreate, parser):
            raise ValueError("Idempotency key was already used for another request")
        return existing
    return submission


def dispatch_next(db: Session) -> bool:
    now = datetime.now()
    submission = (db.query(RagSubmission)
                  .filter(RagSubmission.state.in_(("queued", "submitted", "running")), RagSubmission.next_attempt_at <= now)
                  .order_by(RagSubmission.created_at)
                  .with_for_update(skip_locked=True).first())
    if submission is None:
        return False
    try:
        if submission.state == "queued":
            run = run_rag_pipeline_deployment(submission.report_id, submission.recreate, submission.parser,
                                              idempotency_key=submission.id)
            submission.state = "submitted"
            submission.flow_run_id = str(run.id)
        else:
            run = get_rag_flow_run(submission.flow_run_id)
            if run.state and run.state.is_final():
                submission.state = "finished" if run.state.is_completed() else "failed"
                submission.active_report_id = None
                submission.last_error = None if run.state.is_completed() else "RAG job failed in Prefect"
    except ObjectNotFound:
        if submission.state != "queued":
            submission.state = "failed"
            submission.active_report_id = None
            submission.last_error = "RAG flow run no longer exists in Prefect"
        else:
            submission.last_error = "RAG deployment is unavailable; retrying"
    except Exception:
        logger.exception("Failed to submit RAG job for report {}; retrying", submission.report_id)
        submission.last_error = "Prefect is unavailable; retrying"
    else:
        if submission.state in ("queued", "submitted", "running"):
            submission.last_error = None
    submission.next_attempt_at = datetime.now() + timedelta(seconds=30)
    db.commit()
    return True


def mark_running(db: Session, submission_id: str) -> None:
    submission = db.query(RagSubmission).filter(RagSubmission.id == submission_id).with_for_update().first()
    if submission is None:
        raise ValueError("RAG submission no longer exists")
    if submission.flow_run_id is not None:
        submission.state = "running"
    db.commit()


def pending_statuses(db: Session, statuses: list[dict]) -> list[dict]:
    by_report = {row["report_id"]: row for row in statuses}
    submissions = db.query(RagSubmission).order_by(RagSubmission.created_at.desc(), RagSubmission.id.desc()).all()
    seen = set()
    for submission in submissions:
        if submission.report_id in seen:
            continue
        seen.add(submission.report_id)
        status = by_report.get(submission.report_id)
        if submission.state in ("queued", "submitted", "failed") or not status:
            failed = submission.state in ("failed", "finished")
            by_report[submission.report_id] = {"report_id": submission.report_id,
                                               "status": "FAILED" if failed else ("PARSING" if submission.state == "running" else "PENDING"),
                                               "error": submission.last_error or ("Job finished without updating report status" if failed else ""),
                                               "updated_at": submission.created_at.isoformat()}
    return list(by_report.values())
