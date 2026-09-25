"""Read-only feeds: notifications, audit log, automation runs, dashboard, system status."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app import __version__
from app.config import Settings, get_settings
from app.db import get_db
from app.deps import get_current_user, get_now
from app.enums import AuditEventType, NotificationChannel, RunStatus
from app.errors import NotFoundError
from app.models import AuditEvent, AutomationRun, Notification, User
from app.schemas.dashboard import DashboardOut, SystemStatus
from app.schemas.misc import (
    AuditEventOut,
    AuditList,
    AutomationRunList,
    AutomationRunOut,
    NotificationList,
    NotificationOut,
)
from app.services import automation
from app.services import dashboard as dashboard_service
from app.services.extraction.llm import LLMClient, NotConfiguredClient, get_llm_client

router = APIRouter(prefix="/api", tags=["feeds"])


# ---------------------------------------------------------------- notifications
@router.get("/notifications", response_model=NotificationList)
def list_notifications(
    unread_only: bool = False,
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> NotificationList:
    base = (Notification.user_id == user.id) & (Notification.channel == NotificationChannel.IN_APP)
    stmt = select(Notification).where(base)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = db.scalars(stmt.order_by(Notification.created_at.desc()).limit(limit)).all()
    unread = db.scalar(select(func.count()).select_from(Notification).where(base, Notification.read_at.is_(None))) or 0
    return NotificationList(items=[NotificationOut.model_validate(n) for n in rows], unread=unread)


@router.post("/notifications/{notification_id}/read", status_code=204)
def mark_read(
    notification_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
) -> None:
    note = db.scalar(select(Notification).where(Notification.id == notification_id, Notification.user_id == user.id))
    if note is None:
        raise NotFoundError("Notification not found")
    if note.read_at is None:
        note.read_at = now
    db.commit()


@router.post("/notifications/read-all", status_code=204)
def mark_all_read(
    db: Session = Depends(get_db), user: User = Depends(get_current_user), now: datetime = Depends(get_now)
) -> None:
    db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.channel == NotificationChannel.IN_APP, Notification.read_at.is_(None))
        .values(read_at=now)
    )
    db.commit()


# ---------------------------------------------------------------- audit
@router.get("/audit", response_model=AuditList)
def list_audit(
    obligation_id: uuid.UUID | None = None,
    event_type: list[AuditEventType] | None = Query(None),
    before: int | None = Query(None, description="cursor: return events with id < before"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AuditList:
    stmt = select(AuditEvent).where(AuditEvent.user_id == user.id)
    if obligation_id:
        stmt = stmt.where(AuditEvent.obligation_id == obligation_id)
    if event_type:
        stmt = stmt.where(AuditEvent.event_type.in_(event_type))
    if before:
        stmt = stmt.where(AuditEvent.id < before)
    rows = list(db.scalars(stmt.order_by(AuditEvent.id.desc()).limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    return AuditList(
        items=[AuditEventOut.model_validate(e) for e in rows],
        next_before=rows[-1].id if has_more and rows else None,
    )


# ---------------------------------------------------------------- automation
@router.get("/automation/runs", response_model=AutomationRunList)
def list_runs(
    workflow_key: str | None = None,
    status: list[RunStatus] | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AutomationRunList:
    # A user sees their own runs plus system-wide runs (e.g. the scheduled monitor, which carries only counts).
    scope = or_(AutomationRun.user_id == user.id, AutomationRun.user_id.is_(None))
    stmt = select(AutomationRun).where(scope)
    if workflow_key:
        stmt = stmt.where(AutomationRun.workflow_key == workflow_key)
    if status:
        stmt = stmt.where(AutomationRun.status.in_(status))
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.scalars(stmt.order_by(AutomationRun.started_at.desc()).limit(limit).offset(offset)).all()
    return AutomationRunList(items=[AutomationRunOut.for_viewer(r) for r in rows], total=total)


@router.get("/automation/summary")
def automation_summary(
    hours: int = Query(24, ge=1, le=24 * 30),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
) -> dict:
    since = now - timedelta(hours=hours)
    return {"since": since, "workflows": automation.summary(db, since)}


# ---------------------------------------------------------------- dashboard + status
@router.get("/dashboard", response_model=DashboardOut)
def get_dashboard(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
) -> DashboardOut:
    return DashboardOut.model_validate(dashboard_service.build(db, user, now, settings))


_n8n_cache: dict[str, object] = {"at": 0.0, "value": {"reachable": False, "checked": False}}


def _n8n_health(settings: Settings) -> dict:
    if time.monotonic() - float(_n8n_cache["at"]) < 10:  # avoid hammering n8n from a polling UI
        return _n8n_cache["value"]  # type: ignore[return-value]
    try:
        r = httpx.get(f"{settings.n8n_base_url.rstrip('/')}/healthz", timeout=1.5)
        value = {"reachable": r.is_success, "checked": True}
    except httpx.HTTPError:
        value = {"reachable": False, "checked": True}
    _n8n_cache.update(at=time.monotonic(), value=value)
    return value


@router.get("/system/status", response_model=SystemStatus)
def system_status(
    _: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    now: datetime = Depends(get_now),
    llm: LLMClient = Depends(get_llm_client),
) -> SystemStatus:
    return SystemStatus(
        now=now,
        email_delivery="local_test_inbox" if settings.mail_goes_to_local_sink else "smtp",
        llm_provider=settings.llm_provider,
        llm_model=None if isinstance(llm, NotConfiguredClient) else llm.model,
        llm_configured=not isinstance(llm, NotConfiguredClient),  # the client the extractor will actually use, whatever the provider
        calendar_provider=settings.calendar_provider,
        n8n=_n8n_health(settings),
        version=__version__,
    )
