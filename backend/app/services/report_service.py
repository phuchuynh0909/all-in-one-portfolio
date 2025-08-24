from typing import List
from app.schemas.report import Report
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
