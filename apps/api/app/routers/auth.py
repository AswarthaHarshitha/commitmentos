from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import enforce, get_current_user, get_now, limiter
from app.enums import AuditEventType
from app.errors import ConflictError, ForbiddenError, UnauthorizedError
from app.models import User
from app.ratelimit import client_ip
from app.schemas.user import LoginRequest, RegisterRequest, UserOut, UserPreferences, UserUpdate
from app.security import create_access_token, hash_password, verify_password
from app.services import audit
from app.services.audit import Actor

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=settings.jwt_ttl_minutes * 60,
        httponly=True,  # JS can never read the session
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def _user_out(user: User) -> UserOut:
    return UserOut.model_validate(
        {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "timezone": user.timezone,
            "preferences": UserPreferences.model_validate(user.preferences or {}),
            "created_at": user.created_at,
        }
    )


def _authenticate(db: Session, email: str, password: str, request: Request, settings: Settings) -> User:
    email = email.strip().lower()
    enforce(limiter("login-ip", settings.rate_limit_login_per_minute, 60), client_ip(request))
    enforce(limiter("login-account", settings.rate_limit_login_per_minute, 60), email)
    user = db.scalar(select(User).where(User.email == email))
    # verify_password() always does the same amount of work, even for an unknown email
    ok = verify_password(password, user.password_hash if user else None)
    if not ok or user is None or not user.is_active:
        raise UnauthorizedError("Incorrect email or password", code="INVALID_CREDENTIALS")
    return user


@router.post("/register", response_model=UserOut, status_code=201)
def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    now: datetime = Depends(get_now),
) -> UserOut:
    if not settings.allow_registration:
        raise ForbiddenError("Registration is disabled on this instance", code="REGISTRATION_DISABLED")
    enforce(limiter("register", settings.rate_limit_register_per_hour, 3600), client_ip(request))
    email = body.email.strip().lower()
    user = User(
        email=email,
        password_hash=hash_password(body.password),
        display_name=body.display_name.strip(),
        timezone=body.timezone,
        preferences={},
        last_login_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("An account with this email already exists", code="EMAIL_TAKEN") from exc
    audit.record(db, AuditEventType.SECURITY, "Account created", user_id=user.id, actor=Actor.user(user), now=now)
    token, _ = create_access_token(user.id, user.token_version, settings)
    db.commit()
    _set_session_cookie(response, token, settings)
    return _user_out(user)


@router.post("/login", response_model=UserOut)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    now: datetime = Depends(get_now),
) -> UserOut:
    user = _authenticate(db, body.email, body.password, request, settings)
    user.last_login_at = now
    token, _ = create_access_token(user.id, user.token_version, settings)
    db.commit()
    _set_session_cookie(response, token, settings)
    return _user_out(user)


@router.post("/token", summary="OAuth2 password flow: returns a bearer token for scripts / Swagger 'Authorize'")
def token(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    now: datetime = Depends(get_now),
) -> dict[str, str]:
    user = _authenticate(db, form.username, form.password, request, settings)
    access, expires = create_access_token(user.id, user.token_version, settings)
    db.commit()
    return {"access_token": access, "token_type": "bearer", "expires_at": expires.isoformat()}


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(get_current_user),
) -> Response:
    user.token_version += 1  # revokes every session/token issued so far
    db.commit()
    response.delete_cookie(settings.cookie_name, path="/")
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return _user_out(user)


@router.patch("/me", response_model=UserOut)
def update_me(
    body: UserUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
) -> UserOut:
    if body.display_name is not None:
        user.display_name = body.display_name.strip()
    if body.timezone is not None:
        user.timezone = body.timezone
    if body.preferences is not None:
        user.preferences = body.preferences.model_dump(exclude_none=False)
    user.updated_at = now
    audit.record(db, AuditEventType.SECURITY, "Profile / preferences updated", user_id=user.id, actor=Actor.user(user), now=now)
    db.commit()
    return _user_out(user)

