from sqlalchemy import Column, Integer, Text, TIMESTAMP, text
from app.db.base import Base


class ReportSummary(Base):
    """Store user-created summaries for research reports."""
    __tablename__ = "report_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_id = Column(Integer, unique=True, nullable=False, index=True)
    summary = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP"))

