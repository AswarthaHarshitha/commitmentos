"""Password hashing, JWT sessions, signed one-click action tokens, webhook secret checks."""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.config import Settings, get_settings
from app.errors import UnauthorizedError

_ALGORITHM = "HS256"
_ISSUER = "commitmentos"

# argon2id. Tests use cheap parameters purely for speed; production uses the library defaults.
_hasher = (
    PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    if get_settings().app_env == "test"
    else PasswordHasher()
)
# Verified against when the account does not exist, so login timing does not reveal valid emails.
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")

MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _secret(settings: Settings) -> str:
    secret = settings.jwt_secret.get_secret_value()
    if len(secret) < 32:
        raise RuntimeError("JWT_SECRET must be set to at least 32 characters")
    return secret


def create_access_token(user_id: uuid.UUID, token_version: int, settings: Settings | None = None) -> tuple[str, datetime]:
    """Session tokens are security, not business logic: they use the REAL clock, never the
    (test-adjustable) app clock, so time-travelling in the test stack cannot expire or pre-date a session."""
    settings = settings or get_settings()
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=settings.jwt_ttl_minutes)
    claims = {
        "iss": _ISSUER,
        "typ": "access",
        "sub": str(user_id),
        "tv": token_version,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, _secret(settings), algorithm=_ALGORITHM), expires


def decode_access_token(token: str, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    try:
        claims = jwt.decode(
            token,
            _secret(settings),
            algorithms=[_ALGORITHM],  # pinned: never trust the token's own "alg"
            issuer=_ISSUER,
            options={"require": ["exp", "iat", "sub", "iss", "typ"]},
        )
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("Invalid or expired session") from exc
    if claims.get("typ") != "access":
        raise UnauthorizedError("Invalid or expired session")
    return claims


ActionName = Literal["complete"]


def create_action_token(user_id: uuid.UUID, obligation_id: uuid.UUID, action: ActionName, now: datetime, settings: Settings | None = None) -> str:
    """Signed, expiring link token used in reminder emails ("mark as done").

    It authorises exactly one action on exactly one obligation and is redeemed with a POST from
    the web app (never a GET), so mail scanners that pre-fetch links cannot trigger it.
    """
    settings = settings or get_settings()
    claims = {
        "iss": _ISSUER,
        "typ": "action",
        "act": action,
        "sub": str(user_id),
        "oid": str(obligation_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.action_token_ttl_hours)).timestamp()),
    }
    return jwt.encode(claims, _secret(settings), algorithm=_ALGORITHM)


def decode_action_token(token: str, now: datetime | None = None, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    try:
        claims = jwt.decode(
            token,
            _secret(settings),
            algorithms=[_ALGORITHM],
            issuer=_ISSUER,
            # exp/iat are checked by hand below, against the *app* clock: these links are minted and
            # judged on the same (test-adjustable) business timeline as the reminders that carry them.
            options={"require": ["exp", "iat", "sub", "iss", "typ", "act", "oid"], "verify_exp": False, "verify_iat": False},
        )
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("This link is invalid or has expired") from exc
    if claims.get("typ") != "action":
        raise UnauthorizedError("This link is invalid or has expired")
    if claims["exp"] < int((now or datetime.now(UTC)).timestamp()):
        raise UnauthorizedError("This link is invalid or has expired")
    return claims


def constant_time_equals(provided: str | None, expected: str) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode(), expected.encode())
