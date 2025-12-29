from typing import List, Optional
from app.schemas.report import Report, ReportDetail
from app.stores.raw_wichart_report import WichartReportStore


async def get_reports(symbol: str | None = None) -> List[Report]:
    """Get reports from the store, optionally filtered by symbol."""
    store = WichartReportStore()
    df = store.get_data(mack=symbol)
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
            mack=row['mack'],
            tenbaocao=row['tenbaocao'],
            url=row['url'],
            nguon=row['nguon'],
            ngaykn=row['ngaykn'],
            rsnganh=row['rsnganh'],
        )
        reports.append(report)
    
    return reports


async def get_report_by_id(report_id: int) -> Optional[ReportDetail]:
    """Get a single report by its ID from wichart_reports detail table."""
    store = WichartReportStore()
    
    # Get detail from wichart_reports table
    df = store.get_detail(report_id)
    if df is None or df.empty:
        return None
    
    row = df.iloc[0]
    
    return ReportDetail(
        id=row['document_id'],
        mack=row.get('stock_symbol'),
        tenbaocao=row.get('report_title', ''),
        url=row.get('pdf_url', ''),
        nguon=row.get('source', ''),
        ngaykn=row.get('report_date'),
        rsnganh=row.get('industry_research'),
        # llm_summary is used for both AI summary and user edits
        llm_summary=row.get('llm_summary'),
        clean_content=row.get('clean_content'),
        recommendation=row.get('recommendation'),
        report_category=row.get('report_category'),
        status=row.get('status'),
    )


async def update_report_summary(report_id: int, summary: str) -> bool:
    """Update llm_summary field in wichart_reports table."""
    store = WichartReportStore()
    return store.update_summary(report_id, summary)


async def sync_latest_reports(limit: int = 100) -> dict:
    """Sync latest reports from raw_wichart_report to wichart_reports."""
    store = WichartReportStore()
    return store.sync_latest_reports(limit=limit)
