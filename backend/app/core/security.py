"""Password hashing and JWT minting.

Deliberately free of FastAPI and SQLAlchemy imports so it can be tested
without an app or a database. ``bcrypt`` is used directly rather than through
``passlib``: passlib is effectively unmaintained and its bcrypt-4.x backend
detection is a known source of spurious warnings and breakage.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from loguru import logger

from app.core.settings import settings

ALGORITHM = "HS256"

# bcrypt hashes at most 72 bytes and silently ignores the rest, which would
# make two different long passwords interchangeable.
_MAX_PASSWORD_BYTES = 72


class TokenError(Exception):
    """A token could not be decoded: malformed, expired, or wrongly signed."""


def _resolve_secret_key() -> str:
    """The HS256 signing key, or a loud ephemeral stand-in outside production."""
    if settings.auth_secret_key:
        return settings.auth_secret_key
    if settings.environment == "production":
        raise RuntimeError(
            "APP_AUTH_SECRET_KEY is not set. Refusing to start in production "
            "with an ephemeral signing key — every restart would log everyone "
            "out, and there is no committed default on purpose."
        )
    logger.warning(
        "APP_AUTH_SECRET_KEY is not set — signing tokens with an ephemeral key. "
        "Every restart invalidates all sessions. Set it in .env to persist logins."
    )
    return secrets.token_urlsafe(48)


# Resolved once per process: regenerating per call would mean no token ever
# verified against the key that signed it.
_SECRET_KEY = _resolve_secret_key()


def hash_password(password: str) -> str:
    """Return a salted bcrypt digest of ``password``."""
    if not password:
        raise ValueError("password must not be empty")
    encoded = password.encode("utf-8")
    if len(encoded) > _MAX_PASSWORD_BYTES:
        raise ValueError(
            f"password must be at most {_MAX_PASSWORD_BYTES} bytes; bcrypt "
            "ignores anything beyond that"
        )
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Whether ``password`` matches ``password_hash``.

    A malformed or truncated stored hash reads as "no" rather than raising —
    a corrupt column should fail the login, not 500 the endpoint.
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(
    subject: str, expires_delta: timedelta | None = None
) -> tuple[str, datetime]:
    """Mint a token for ``subject`` (the username). Returns (token, expiry)."""
    now = datetime.now(timezone.utc)
    expires_at = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(days=settings.auth_token_ttl_days)
    )
    token = jwt.encode(
        {"sub": subject, "iat": now, "exp": expires_at},
        _SECRET_KEY,
        algorithm=ALGORITHM,
    )
    return token, expires_at


def decode_access_token(token: str) -> str:
    """Return the token's subject, or raise ``TokenError``."""
    try:
        payload = jwt.decode(token, _SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise TokenError("token carries no subject")
    return subject
