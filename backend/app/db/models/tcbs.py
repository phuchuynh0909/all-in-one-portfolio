from sqlalchemy import Column, DateTime, Integer, String, TIMESTAMP, text

from app.db.base import Base


class TcbsOAuthToken(Base):
    """The single TCBS MCP credential set, written by the host-side login CLI.

    One row per install: the connector authorizes one TCBS account, matching the
    app's single-portfolio design. ``id`` is pinned to 1 by the store so a second
    login overwrites rather than accumulating stale grants.

    The client secret and refresh token are stored as issued. The database is
    not reachable from outside the compose network, and these are no more
    sensitive than the broker credentials already in the root ``.env``.
    """

    __tablename__ = "tcbs_oauth_tokens"

    id = Column(Integer, primary_key=True, autoincrement=False)
    client_id = Column(String(255), nullable=False)
    client_secret = Column(String(255), nullable=True)
    access_token = Column(String(2048), nullable=False)
    refresh_token = Column(String(2048), nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(
        TIMESTAMP,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
    )
