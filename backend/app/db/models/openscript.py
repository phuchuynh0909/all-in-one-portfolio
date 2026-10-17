from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.mysql import LONGTEXT

from app.db.base import Base


class OpenScript(Base):
    __tablename__ = "openscripts"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    id = Column(String(72), primary_key=True)
    title = Column(String(255), nullable=False)
    source = Column(Text().with_variant(LONGTEXT(), "mysql"), nullable=False)
    applied_source = Column(Text().with_variant(LONGTEXT(), "mysql"), nullable=True)
    visible = Column(Boolean, nullable=False, server_default=text("0"))
