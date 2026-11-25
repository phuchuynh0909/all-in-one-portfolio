from sqlalchemy import BigInteger, Boolean, Column, DateTime, String, Text
from sqlalchemy.dialects.mysql import DATETIME

from app.db.base import Base


class RagSubmission(Base):
    __tablename__ = "rag_submissions"

    id = Column(String(36), primary_key=True)
    report_id = Column(BigInteger, nullable=False, index=True)
    active_report_id = Column(BigInteger, nullable=True, unique=True)
    recreate = Column(Boolean, nullable=False, default=False)
    parser = Column(String(32), nullable=True)
    state = Column(String(16), nullable=False, default="queued", index=True)
    flow_run_id = Column(String(36), nullable=True)
    created_at = Column(DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=False)
    next_attempt_at = Column(DateTime().with_variant(DATETIME(fsp=6), "mysql"), nullable=False)
    last_error = Column(Text, nullable=True)
