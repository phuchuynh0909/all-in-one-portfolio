from pydantic import BaseModel
from typing import List, Optional

class Report(BaseModel):
    id: int
    mack: Optional[str] = None
    tenbaocao: str
    url: str
    nguon: str
    ngaykn: Optional[str] = None
    rsnganh: Optional[str] = None

class ReportResponse(BaseModel):
    reports: List[Report]
