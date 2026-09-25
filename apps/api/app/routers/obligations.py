from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import get_current_user, get_now
from app.enums import (
    ApprovalAction,
    ApprovalStatus,
    ObligationStatus,
    ObligationType,
    Priority,
    SourceType,
)
from app.errors import ConflictError, ValidationFailed
from app.models import (
    ApprovalRequest,
    AutomationRun,
    CalendarEvent,
    Notification,
    Obligation,
    Source,
    User,
)
from app.schemas.detail import ObligationDetail, Understanding
from app.schemas.misc import (
    ApprovalOut,
    AutomationRunOut,
    CalendarEventOut,
    NotificationOut,
    TimelineEntry,
)
from app.schemas.obligation import (
    ActionResult,
    CompleteRequest,
    DismissRequest,
    ObligationCreate,
    ObligationList,
    ObligationOut,
    ObligationPatch,
    ScheduleRequest,
    SnoozeRequest,
    SourceOut,
)
from app.services import approvals, detail, followups
from app.services import obligations as svc
from app.services.dispatch import dispatch_approval
from app.services.n8n_client import N8nClient, get_n8n_client

router = APIRouter(prefix="/api/obligations", tags=["obligations"])


def _out(ob: Obligation) -> ObligationOut:
    return ObligationOut.model_validate(ob)


@router.post("", response_model=ObligationOut, status_code=201)
def create_obligation(
    body: ObligationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
) -> ObligationOut:
    ob = svc.create_manual(db, user, body, now, settings)
    db.commit()
    return _out(ob)


@router.get("", response_model=ObligationList)
def list_obligations(
    view: str = Query("all", description="inbox | active | review | overdue | closed | upcoming | all"),
    status: list[ObligationStatus] | None = Query(None),
    type: list[ObligationType] | None = Query(None),
    priority: list[Priority] | None = Query(None),
    source: list[SourceType] | None = Query(None),
    q: str | None = Query(None, max_length=200),
    due_before: datetime | None = None,
    due_after: datetime | None = None,
    has_due: bool | None = None,
    sort: str = Query("due", pattern="^(due|created|priority)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
) -> ObligationList:
    if view not in svc.VIEWS:
        raise ValidationFailed(f"Unknown view {view!r}; expected one of {sorted(svc.VIEWS)}")
    for name, value in (("due_before", due_before), ("due_after", due_after)):
        if value is not None and value.tzinfo is None:
            raise ValidationFailed(f"{name} must include a UTC offset")
    stmt = svc.list_query(
        user, view=view, statuses=status, types=type, priorities=priority, sources=source, q=q,
        due_before=due_before, due_after=due_after, has_due=has_due, now=now,
    )
    rows, total = svc.paginate(db, stmt, sort=sort, limit=limit, offset=offset)
    return ObligationList(items=[_out(o) for o in rows], total=total, limit=limit, offset=offset)


@router.get("/{obligation_id}", response_model=ObligationDetail)
def get_obligation(
    obligation_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
) -> ObligationDetail:
    ob = svc.get_owned(db, user, obligation_id)
    sources = list(db.scalars(select(Source).where(Source.obligation_id == ob.id).order_by(Source.created_at)))
    primary = next((s for s in sources if s.role == "PRIMARY"), sources[0] if sources else None)
    notes = list(db.scalars(select(Notification).where(Notification.obligation_id == ob.id).order_by(Notification.created_at.desc()).limit(50)))
    apps = list(db.scalars(select(ApprovalRequest).where(ApprovalRequest.obligation_id == ob.id).order_by(ApprovalRequest.created_at.desc())))
    cal = list(db.scalars(select(CalendarEvent).where(CalendarEvent.obligation_id == ob.id).order_by(CalendarEvent.start_at)))
    runs = list(
        db.scalars(
            select(AutomationRun)
            .where((AutomationRun.obligation_id == ob.id) | (AutomationRun.correlation_id == str(ob.id)))
            .order_by(AutomationRun.started_at.desc())
            .limit(20)
        )
    )
    tz = svc.user_zone(user, settings)
    return ObligationDetail(
        obligation=_out(ob),
        understanding=Understanding.model_validate(detail.build_understanding(ob, primary, tz.key)),
        sources=[SourceOut.model_validate(s) for s in sources],
        notifications=[NotificationOut.model_validate(n) for n in notes],
        approvals=[ApprovalOut.model_validate(a) for a in apps],
        calendar_events=[CalendarEventOut.model_validate(c) for c in cal],
        runs=[AutomationRunOut.for_viewer(r) for r in runs],
        suggested_next_action=detail.suggest_next_action(
            ob, now, has_calendar_event=bool(cal), open_follow_up=detail.open_follow_up(db, ob)
        ),
        now=now,
        timezone=tz.key,
    )


@router.get("/{obligation_id}/timeline", response_model=list[TimelineEntry])
def get_timeline(
    obligation_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
) -> list[TimelineEntry]:
    ob = svc.get_owned(db, user, obligation_id)
    return detail.build_timeline(db, ob, user, now, settings)


@router.patch("/{obligation_id}", response_model=ActionResult)
def patch_obligation(
    obligation_id: uuid.UUID,
    body: ObligationPatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
) -> ActionResult:
    ob = svc.get_owned(db, user, obligation_id, lock=True)
    changed = svc.apply_patch(db, user, ob, body, now, settings)
    db.commit()
    return ActionResult(obligation=_out(ob), changed=bool(changed))


@router.post("/{obligation_id}/complete", response_model=ActionResult)
def complete_obligation(
    obligation_id: uuid.UUID,
    body: CompleteRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
) -> ActionResult:
    ob = svc.get_owned(db, user, obligation_id, lock=True)
    changed, spawned = svc.complete(db, user, ob, now, settings, via="DASHBOARD", note=body.note if body else None)
    db.commit()
    return ActionResult(obligation=_out(ob), changed=changed, spawned_next=_out(spawned) if spawned else None)


@router.post("/{obligation_id}/dismiss", response_model=ActionResult)
def dismiss_obligation(
    obligation_id: uuid.UUID,
    body: DismissRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
) -> ActionResult:
    ob = svc.get_owned(db, user, obligation_id, lock=True)
    changed = svc.dismiss(db, user, ob, now, reason=body.reason if body else None)
    db.commit()
    return ActionResult(obligation=_out(ob), changed=changed)


@router.post("/{obligation_id}/snooze", response_model=ActionResult)
def snooze_obligation(
    obligation_id: uuid.UUID,
    body: SnoozeRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
) -> ActionResult:
    body = body or SnoozeRequest()
    ob = svc.get_owned(db, user, obligation_id, lock=True)
    svc.snooze(db, user, ob, now, settings, hours=body.hours, until=body.until)
    db.commit()
    return ActionResult(obligation=_out(ob), changed=True)


@router.post("/{obligation_id}/approve", response_model=ActionResult, summary="Accept a detected commitment")
def approve_detection(
    obligation_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
) -> ActionResult:
    ob = svc.get_owned(db, user, obligation_id, lock=True)
    changed = svc.accept(db, user, ob, now, settings)
    db.commit()
    return ActionResult(obligation=_out(ob), changed=changed)


@router.post("/{obligation_id}/reopen", response_model=ActionResult)
def reopen_obligation(
    obligation_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
) -> ActionResult:
    ob = svc.get_owned(db, user, obligation_id, lock=True)
    changed = svc.reopen(db, user, ob, now, settings)
    db.commit()
    return ActionResult(obligation=_out(ob), changed=changed)


@router.post("/{obligation_id}/schedule", response_model=ApprovalOut, status_code=202)
def schedule_obligation(
    obligation_id: uuid.UUID,
    background: BackgroundTasks,
    body: ScheduleRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
    n8n: N8nClient = Depends(get_n8n_client),
) -> ApprovalOut:
    """Put time on the calendar. The user's click is the approval; n8n performs the action."""
    body = body or ScheduleRequest()
    ob = svc.get_owned(db, user, obligation_id, lock=True)
    if ob.status in (ObligationStatus.COMPLETED, ObligationStatus.DISMISSED):
        raise ConflictError("Reopen this obligation before scheduling it", code="OBLIGATION_CLOSED")
    start = body.start_at or ob.due_at
    if start is None:
        raise ValidationFailed("This obligation has no deadline to schedule around; pass start_at")
    if ob.obligation_type not in (ObligationType.APPOINTMENT, ObligationType.INTERVIEW) and body.start_at is None:
        start = start - timedelta(minutes=body.duration_minutes)  # work block ending at the deadline
    payload = {
        "title": body.title or ob.title,
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(minutes=body.duration_minutes)).isoformat(),
        "timezone": svc.user_zone(user, settings).key,
        "description": f"Created by CommitmentOS for: {ob.title}",
    }
    approval, created = approvals.propose(
        db, user=user, ob=ob, action=ApprovalAction.CREATE_CALENDAR_EVENT,
        title=f"Add to calendar: {ob.title}", payload=payload, proposed_by="USER",
        rationale="You asked CommitmentOS to schedule this.", now=now, settings=settings,
        status=ApprovalStatus.APPROVED, notify=False,
    )
    svc.acknowledge(ob, now)
    db.commit()
    if created:
        background.add_task(dispatch_approval, approval.id, user.id, n8n)
    return ApprovalOut.model_validate(approval)


@router.post("/{obligation_id}/follow-up", response_model=ApprovalOut, status_code=201)
def create_follow_up(
    obligation_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
) -> ApprovalOut:
    """Draft a follow-up email. Nothing is sent until the user approves the draft."""
    ob = svc.get_owned(db, user, obligation_id, lock=True)
    if ob.status in (ObligationStatus.COMPLETED, ObligationStatus.DISMISSED):
        raise ConflictError("This obligation is closed", code="OBLIGATION_CLOSED")
    if not ob.counterparty_email:
        raise ValidationFailed("There is no recipient for a follow-up: add a counterparty email to this obligation first")
    draft = followups.draft_follow_up(ob, user, now, svc.user_zone(user, settings))
    approval, _ = approvals.propose(
        db, user=user, ob=ob, action=ApprovalAction.SEND_FOLLOW_UP,
        title=f"Send follow-up to {ob.counterparty_name or ob.counterparty_email}",
        payload={"to": ob.counterparty_email, **draft}, proposed_by="SYSTEM",
        rationale="Drafted from a template. Review and edit it; it will not be sent until you approve.",
        now=now, settings=settings,
    )
    svc.acknowledge(ob, now)
    db.commit()
    return ApprovalOut.model_validate(approval)

