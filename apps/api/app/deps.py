"""FastAPI dependencies: settings, clock, authentication, CSRF guard, n8n secret, rate limits."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.clock import clock
from app.config import Settings, get_settings
from app.db import get_db
from app.errors import ForbiddenError, RateLimited, UnauthorizedError
from app.models import User
from app.ratelimit import SlidingWindowLimiter
from app.security import constant_time_equals, decode_access_token

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Module-level limiters (per process). Built lazily so tests can reset/replace them.
_limiters: dict[str, SlidingWindowLimiter] = {}


def limiter(name: str, limit: int, window_seconds: float) -> SlidingWindowLimiter:
    key = f"{name}:{limit}:{window_seconds}"
    if key not in _limiters:
        _limiters[key] = SlidingWindowLimiter(limit, window_seconds)
    return _limiters[key]


def reset_limiters() -> None:
    for lim in _limiters.values():
        lim.reset()


def enforce(lim: SlidingWindowLimiter, key: str) -> None:
    allowed, retry_after = lim.check(key)
    if not allowed:
        raise RateLimited(
            "Too many attempts, please wait a moment and try again",
            extra={"retry_after": max(1, int(retry_after) + 1)},
        )


def get_now() -> datetime:
    return clock.now()


def _token_from_request(request: Request, settings: Settings) -> tuple[str | None, str]:
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip() or None, "bearer"
    return request.cookies.get(settings.cookie_name), "cookie"


def _allowed_origins(settings: Settings) -> set[str]:
    return {o.rstrip("/") for o in [*settings.cors_origins, settings.public_web_url]}


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    token, kind = _token_from_request(request, settings)
    if not token:
        raise UnauthorizedError("Not authenticated")
    claims = decode_access_token(token, settings)
    try:
        user_id = uuid.UUID(claims["sub"])
    except ValueError as exc:
        raise UnauthorizedError("Invalid session") from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active or claims.get("tv") != user.token_version:
        raise UnauthorizedError("Session is no longer valid")

    # CSRF: a browser attaches cookies automatically, so for cookie-authenticated state-changing
    # requests we require any Origin header to be one of ours (SameSite=Lax is the first line of defence).
    if kind == "cookie" and request.method not in SAFE_METHODS:
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in _allowed_origins(settings):
            raise ForbiddenError("Cross-origin request blocked", code="CSRF_BLOCKED")

    enforce(limiter("api", settings.rate_limit_api_per_minute, 60), str(user.id))
    return user


def require_n8n(
    x_webhook_secret: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Service-to-service auth for n8n -> API. Deliberately separate from user sessions."""
    if not constant_time_equals(x_webhook_secret, settings.n8n_inbound_secret.get_secret_value()):
        raise UnauthorizedError("Invalid webhook secret", code="INVALID_WEBHOOK_SECRET")
