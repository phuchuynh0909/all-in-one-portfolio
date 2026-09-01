"""Read and write the single TCBS MCP credential row.

The login CLI runs on the host; the code that spends the token runs in the
backend container. MySQL is the one thing both reach, which is the same reason
``backend/scripts/manage_users.py`` talks to it directly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.db.base import SessionLocal
from app.db.models.tcbs import TcbsOAuthToken

logger = logging.getLogger(__name__)

#: There is only ever one credential set, so the row is pinned rather than
#: appended: a second login must replace the first, not shadow it.
ROW_ID = 1


@dataclass
class TcbsCredentials:
    client_id: str
    client_secret: str | None
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None

    def is_expired(self, skew_seconds: int = 60) -> bool:
        """Whether the access token is spent, or close enough to it.

        An unknown expiry is *not* treated as expired: the token is used until
        the server rejects it, and the 401-refresh path recovers. Guessing here
        would burn refreshes on tokens that were still good.
        """
        if self.expires_at is None:
            return False
        expires = self.expires_at
        if expires.tzinfo is None:
            # MySQL DATETIME comes back naive; it was stored as UTC.
            expires = expires.replace(tzinfo=timezone.utc)
        return expires - timedelta(seconds=skew_seconds) <= datetime.now(timezone.utc)


def load() -> TcbsCredentials | None:
    """The stored credentials, or None when nobody has logged in."""
    session = SessionLocal()
    try:
        row = session.get(TcbsOAuthToken, ROW_ID)
        if row is None:
            return None
        return TcbsCredentials(
            client_id=row.client_id,
            client_secret=row.client_secret,
            access_token=row.access_token,
            refresh_token=row.refresh_token,
            expires_at=row.expires_at,
        )
    finally:
        session.close()


def save(creds: TcbsCredentials) -> None:
    """Insert or replace the credential row."""
    session = SessionLocal()
    try:
        row = session.get(TcbsOAuthToken, ROW_ID)
        if row is None:
            row = TcbsOAuthToken(id=ROW_ID)
            session.add(row)
        row.client_id = creds.client_id
        row.client_secret = creds.client_secret
        row.access_token = creds.access_token
        row.refresh_token = creds.refresh_token
        row.expires_at = creds.expires_at
        session.commit()
    finally:
        session.close()


def clear() -> bool:
    """Delete the credential row. True when one was there to delete."""
    session = SessionLocal()
    try:
        row = session.get(TcbsOAuthToken, ROW_ID)
        if row is None:
            return False
        session.delete(row)
        session.commit()
        return True
    finally:
        session.close()
