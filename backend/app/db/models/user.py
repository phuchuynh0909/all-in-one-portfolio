from sqlalchemy import Boolean, Column, Integer, String, TIMESTAMP, text

from app.db.base import Base


class User(Base):
    """An application login. Seeded by ``app/scripts/create_user.py``.

    Carries no ownership of data: every authenticated user sees the same single
    portfolio. The row exists so one person can be revoked (``is_active``)
    without rotating everyone else's tokens.
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, server_default=text("1"))
    created_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
