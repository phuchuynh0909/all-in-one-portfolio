from sqlalchemy import Column, ForeignKey, Integer, JSON, String

from app.db.base import Base


class ChartLayout(Base):
    __tablename__ = "chart_layouts"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    id = Column(String(72), primary_key=True)
    name = Column(String(60), nullable=False)
    indicators = Column(JSON, nullable=False)
    params = Column(JSON, nullable=False)
    scripts = Column(JSON, nullable=False)
    chart_state = Column(JSON, nullable=True)


class ChartPreference(Base):
    __tablename__ = "chart_preferences"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    active_layout_id = Column(String(72), nullable=True)
