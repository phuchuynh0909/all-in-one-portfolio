from datetime import datetime
from typing import List, Optional

import pandas as pd

from app.schemas.report import Report, ReportCreate, ReportDetail
from app.stores.raw_wichart_report import ReportIdTaken, WichartReportStore, _query_raw


def _none_if_nan(value):
    return None if pd.isna(value) else value


def _query_raw_reports(symbol: str | None = None, report_id: int | None = None) -> pd.DataFrame:
    """The crawled report feed, from MySQL.

    Thin wrapper over the store's reader so the feed is queried in exactly one
    place; it returns a couple of columns more than this module reads (the
    detail seed needs them) and every caller here goes through ``row.get``.
    """
    return _query_raw(report_id=report_id, mack=symbol)


async def get_reports(symbol: str | None = None) -> List[Report]:
    df = _query_raw_reports(symbol=symbol)
    if df is None or df.empty:
        return []
    
    reports = []

    ## order by ngaykn desc
    df = df.sort_values(by='ngaykn', ascending=False)

    ## remove duplicates
    df = df.drop_duplicates(subset=['id'])

    for _, row in df.iterrows():
        report = Report(
            id=row['id'],
            mack=_none_if_nan(row.get('mack')),
            tenbaocao=_none_if_nan(row.get('tenbaocao')) or "",
            url=_none_if_nan(row.get('url')) or "",
            nguon=_none_if_nan(row.get('nguon')) or "",
            ngaykn=_none_if_nan(row.get('ngaykn')),
            rsnganh=_none_if_nan(row.get('rsnganh')),
        )
        reports.append(report)
    
    return reports


async def get_report_by_id(report_id: int) -> Optional[ReportDetail]:
    df = _query_raw_reports(report_id=report_id)
    if df is None or df.empty:
        return None
    
    row = df.iloc[0]
    
    return ReportDetail(
        id=row['id'],
        mack=_none_if_nan(row.get('mack')),
        tenbaocao=_none_if_nan(row.get('tenbaocao')) or '',
        url=_none_if_nan(row.get('url')) or '',
        nguon=_none_if_nan(row.get('nguon')) or '',
        ngaykn=_none_if_nan(row.get('ngaykn')),
        rsnganh=_none_if_nan(row.get('rsnganh')),
        llm_summary=None,
        clean_content=None,
        recommendation=None,
        report_category=None,
        status=None,
    )


async def create_report(payload: ReportCreate) -> Report:
    """Add a report by hand to the feed and return it with its id.

    Goes into the same table the crawler fills, so the new report shows up in
    the list, the detail page and the RAG pipeline like any other. ``payload.id``
    pins the feed id (``ReportIdTaken`` if it is in use); left unset, one is
    allocated from the reserved manual band. Note the ``/report/list`` response
    is cached — refetch with ``Cache-Control: no-cache`` to see the new row
    immediately.
    """
    store = WichartReportStore()
    ngaykn = payload.ngaykn or datetime.now()
    report_id = store.create_manual_report(
        report_id=payload.id,
        mack=payload.mack,
        tenbaocao=payload.tenbaocao,
        url=payload.url,
        nguon=payload.nguon,
        ngaykn=ngaykn,
        rsnganh=payload.rsnganh,
    )
    return Report(
        id=report_id,
        mack=payload.mack,
        tenbaocao=payload.tenbaocao,
        url=payload.url,
        nguon=payload.nguon,
        ngaykn=ngaykn,
        rsnganh=payload.rsnganh,
    )


async def update_report_summary(report_id: int, summary: str) -> bool:
    """Update llm_summary field in wichart_reports table."""
    store = WichartReportStore()
    return store.update_summary(report_id, summary)


async def sync_latest_reports(limit: int = 100) -> dict:
    """Sync latest reports from raw_wichart_report to wichart_reports."""
    store = WichartReportStore()
    return store.sync_latest_reports(limit=limit)
