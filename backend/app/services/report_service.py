from typing import List, Optional
import os
import pandas as pd
import clickhouse_connect
from app.schemas.report import Report, ReportDetail
from app.stores.raw_wichart_report import WichartReportStore
from app.core.settings import settings


def _get_clickhouse_client():
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_db,
    )


def _none_if_nan(value):
    return None if pd.isna(value) else value


def _query_raw_reports(symbol: str | None = None, report_id: int | None = None) -> pd.DataFrame:
    table = os.getenv("CLICKHOUSE_WICHART_REPORT_TABLE", "raw_wichart_report")
    base_query = (
        "SELECT id, mack, tenbaocao, url, nguon, ngaykn, rsnganh "
        f"FROM {settings.clickhouse_db}.{table} FINAL"
    )
    conditions: list[str] = []
    params: dict[str, object] = {}

    if symbol:
        conditions.append("mack = %(symbol)s")
        params["symbol"] = symbol
    if report_id is not None:
        conditions.append("id = %(report_id)s")
        params["report_id"] = report_id

    if conditions:
        base_query += " WHERE " + " AND ".join(conditions)
    base_query += " ORDER BY ngaykn DESC, id DESC"

    client = _get_clickhouse_client()
    try:
        result = client.query(base_query, parameters=params if params else None)
        return pd.DataFrame(result.result_rows, columns=result.column_names)
    finally:
        client.close()


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


async def update_report_summary(report_id: int, summary: str) -> bool:
    """Update llm_summary field in wichart_reports table."""
    store = WichartReportStore()
    return store.update_summary(report_id, summary)


async def sync_latest_reports(limit: int = 100) -> dict:
    """Sync latest reports from raw_wichart_report to wichart_reports."""
    store = WichartReportStore()
    return store.sync_latest_reports(limit=limit)
