from pydantic import BaseModel, Field, field_validator
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

class ReportCreate(BaseModel):
    """A report entered by hand, for what the crawler did not pick up.

    Field names mirror the crawled feed (``raw_wichart_report``) so a manual row
    is indistinguishable from a crawled one everywhere downstream. Max lengths
    match that table's columns.
    """

    id: Optional[int] = Field(
        default=None,
        gt=0,
        description=(
            "Feed id to use. Leave unset to allocate one from the reserved "
            "manual band; an explicit id must not already exist, and one inside "
            "the crawler's range may be overwritten by a later crawl."
        ),
    )
    tenbaocao: str = Field(min_length=1, max_length=512, description="Report title")
    url: str = Field(min_length=1, max_length=1024, description="Link to the PDF")
    mack: Optional[str] = Field(default=None, max_length=32, description="Ticker")
    nguon: str = Field(default="manual", max_length=128, description="Source/broker")
    ngaykn: Optional[datetime] = Field(default=None, description="Report date; defaults to now")
    rsnganh: Optional[str] = Field(default=None, max_length=255, description="Sector")

    @field_validator("url")
    @classmethod
    def _http_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return v

    @field_validator("tenbaocao", "nguon")
    @classmethod
    def _stripped_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    @field_validator("mack")
    @classmethod
    def _upper_ticker(cls, v: Optional[str]) -> Optional[str]:
        return (v or "").strip().upper() or None

    @field_validator("rsnganh")
    @classmethod
    def _stripped_optional(cls, v: Optional[str]) -> Optional[str]:
        return (v or "").strip() or None


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
