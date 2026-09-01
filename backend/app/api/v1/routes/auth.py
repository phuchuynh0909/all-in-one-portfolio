"""Application authentication: log in, and identify the caller."""
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password, verify_password
from app.db.base import get_db
from app.db.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])

_BAD_CREDENTIALS = "Incorrect username or password"


@lru_cache(maxsize=1)
def _timing_decoy_hash() -> str:
    """A real bcrypt digest to verify against when the username is unknown.

    Without it a missing user returns immediately while a wrong password costs
    ~250ms of bcrypt, and that difference tells an attacker which usernames
    exist. Computed on the first unknown-username login rather than at import,
    so it costs nothing at startup or on the happy path. The plaintext is
    arbitrary — this digest guards nothing.
    """
    return hash_password("unused")


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.query(User).filter(User.username == payload.username).one_or_none()

    if user is None:
        verify_password(payload.password, _timing_decoy_hash())
        logger.info("auth: login failed — no such user {!r}", payload.username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _BAD_CREDENTIALS)

    if not verify_password(payload.password, user.password_hash):
        logger.info("auth: login failed — bad password for {!r}", payload.username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _BAD_CREDENTIALS)

    if not user.is_active:
        # Same wording as a bad password: whether an account exists but is
        # disabled is not something an unauthenticated caller should learn.
        logger.info("auth: login refused — {!r} is deactivated", payload.username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _BAD_CREDENTIALS)

    token, expires_at = create_access_token(user.username)
    logger.info("auth: {!r} logged in until {}", user.username, expires_at)
    return TokenResponse(access_token=token, expires_at=expires_at)
