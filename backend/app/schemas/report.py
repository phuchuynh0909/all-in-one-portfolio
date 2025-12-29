from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class Report(BaseModel):
    id: int
    mack: Optional[str] = None
    tenbaocao: str
    url: str
    nguon: str
    ngaykn: Optional[datetime] = None
    rsnganh: Optional[str] = None

class ReportResponse(BaseModel):
    reports: List[Report]

class ReportSummaryUpdate(BaseModel):
    summary: str

class ReportDetail(BaseModel):
    id: int
    mack: Optional[str] = None
    tenbaocao: str
    url: str
    nguon: str
    ngaykn: Optional[datetime] = None
    rsnganh: Optional[str] = None
    # Fields from wichart_reports detail table
    clean_content: Optional[str] = None
    llm_summary: Optional[str] = None  # Used for both AI summary and user edits
    recommendation: Optional[str] = None
    report_category: Optional[str] = None
    token_count: Optional[int] = None
    status: Optional[str] = None
