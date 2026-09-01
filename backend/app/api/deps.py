"""The app-wide authentication guard.

Registered on the ``FastAPI`` instance itself, so every router — including any
router added later — is protected without a per-router opt-in. A dependency
rather than ASGI middleware because only a dependency can hand the resolved
``User`` to a handler, show up in OpenAPI, and be swapped out in tests with one
``dependency_overrides`` line.
"""
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger
from sqlalchemy.orm import Session

from app.core.security import TokenError, decode_access_token
from app.db.base import get_db
from app.db.models.user import User

# The only routes reachable without a token. ``/docs`` and ``/openapi.json``
# are absent because FastAPI registers them as Starlette routes, which
# app-level dependencies never touch.
EXEMPT_PATHS = frozenset(
    {
        "/api/v1/health",
        "/api/v1/auth/login",
        # TCBS redirects the browser here after its own login and iOTP prompt,
        # with no Authorization header to carry. The route is safe to leave open
        # because it completes nothing without the unguessable ``state`` the
        # authorize call minted and kept server-side.
        "/api/v1/trading-agents/tcbs/callback",
    }
)

# auto_error=False matters twice: it lets an exempt path through without a
# header (auto_error=True raises 403 before this function runs), and it lets us
# return 401 rather than FastAPI's 403 for a missing credential.
bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    """Resolve the caller, or raise 401. Returns ``None`` on exempt paths."""
    if request.url.path.rstrip("/") in EXEMPT_PATHS:
        return None

    if credentials is None or not credentials.credentials:
        raise _unauthorized("Not authenticated")

    try:
        username = decode_access_token(credentials.credentials)
    except TokenError as exc:
        # Logged at info with the reason: expired and forged look identical
        # from the client side, and telling them apart matters when debugging.
        logger.info("auth: rejected token on {} — {}", request.url.path, exc)
        raise _unauthorized("Invalid or expired token")

    user = db.query(User).filter(User.username == username).one_or_none()
    if user is None:
        logger.info("auth: token names {!r}, which has no row", username)
        raise _unauthorized("Invalid or expired token")
    if not user.is_active:
        logger.info("auth: {!r} is deactivated", username)
        raise _unauthorized("Account is deactivated")

    return user
