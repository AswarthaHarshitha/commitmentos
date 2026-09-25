"""Human approval for anything with an external side effect.

    proposed (PENDING) --user approves--> APPROVED --n8n claims--> EXECUTING --> EXECUTED | FAILED
                       \\--user rejects--> REJECTED         (or EXPIRED / CANCELLED)

n8n can only ever execute rows the *user* moved to APPROVED, and must claim them atomically
first, so a duplicate webhook or a second scheduled pull cannot run an action twice.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import (
    ApprovalAction,
    AuditEventType,
    CalendarEventStatus,
    CalendarProvider,
    NotificationKind,
)
from app.enums import (
    ApprovalStatus as A,
)
from app.enums import (
    ObligationStatus as S,
)
from app.errors import ConflictError, NotFoundError, ValidationFailed
from app.models import ApprovalRequest, CalendarEvent, Obligation, User
from app.schemas.misc import ApprovalPatch
from app.services import audit, automation, followups, lifecycle, messages, notifications
from app.services import obligations as ob_service
from app.services.audit import Actor
from app.services.reminders import policy_for

EXTERNAL_ACTIONS = {ApprovalAction.SEND_FOLLOW_UP, ApprovalAction.CREATE_CALENDAR_EVENT}
_OPEN_STATES = [A.PENDING, A.APPROVED, A.EXECUTING]


class FollowUpPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: EmailStr
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=8000)
    in_reply_to: str | None = None
    thread_id: str | None = None


class CalendarPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    start_at: datetime
    end_at: datetime
    timezone: str = "UTC"
    location: str | None = None
    description: str | None = Field(default=None, max_length=2000)


class EmptyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


_PAYLOADS: dict[ApprovalAction, type[BaseModel]] = {
    ApprovalAction.SEND_FOLLOW_UP: FollowUpPayload,
    ApprovalAction.CREATE_CALENDAR_EVENT: CalendarPayload,
    ApprovalAction.DISMISS_OBLIGATION: EmptyPayload,
    ApprovalAction.COMPLETE_OBLIGATION: EmptyPayload,
}


def validate_payload(action: ApprovalAction, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return _PAYLOADS[action].model_validate(payload).model_dump(mode="json")
    except ValidationError as exc:
        raise ValidationFailed("Invalid action payload: " + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())) from exc


NO_ADDRESS = "There is no address to send a follow-up to: the original email had no sender address. Add their email to the commitment first"


def _require_original_recipient(db: Session, ob: Obligation, user: User, to: str) -> None:
    """A follow-up goes to the address the original email came from, and nowhere else - however the proposal arrived."""
    target = followups.follow_up_target(db, ob, user)
    if target.address is None:
        raise ValidationFailed(NO_ADDRESS)
    if to.strip().lower() != target.address:
        raise ValidationFailed(f"A follow-up can only be addressed to {target.address}, the address the original email came from")


def get_owned(db: Session, user: User, approval_id: uuid.UUID, *, lock: bool = False) -> ApprovalRequest:
    stmt = select(ApprovalRequest).where(ApprovalRequest.id == approval_id, ApprovalRequest.user_id == user.id)
    if lock:
        stmt = stmt.with_for_update()
    approval = db.scalar(stmt)
    if approval is None:
        raise NotFoundError("Approval request not found")
    return approval


def propose(
    db: Session,
    *,
    user: User,
    ob: Obligation,
    action: ApprovalAction,
    title: str,
    payload: dict[str, Any],
    proposed_by: str,
    rationale: str | None,
    now: datetime,
    settings: Settings,
    status: A = A.PENDING,
    notify: bool = True,
    run_id: uuid.UUID | None = None,
) -> tuple[ApprovalRequest, bool]:
    """Create an approval request. Idempotent: an open request of the same kind is returned as-is."""
    payload = validate_payload(action, payload)
    if action == ApprovalAction.SEND_FOLLOW_UP:
        _require_original_recipient(db, ob, user, payload["to"])
    existing = db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.obligation_id == ob.id,
            ApprovalRequest.action_type == action,
            ApprovalRequest.status.in_(_OPEN_STATES),
        )
    )
    if existing is not None:
        return existing, False
    approval = ApprovalRequest(
        user_id=user.id,
        obligation_id=ob.id,
        automation_run_id=run_id,
        action_type=action,
        status=status,
        proposed_by=proposed_by,
        title=title[:300],
        rationale=rationale,
        payload=payload,
        expires_at=now + timedelta(hours=settings.approval_ttl_hours),
        decided_at=now if status == A.APPROVED else None,
        created_at=now,
        updated_at=now,
    )
    db.add(approval)
    db.flush()
    actor = Actor.user(user) if proposed_by == "USER" else (Actor.ai() if proposed_by == "AI" else Actor.system("approvals"))
    audit.record(
        db,
        AuditEventType.APPROVAL_REQUESTED if status == A.PENDING else AuditEventType.APPROVAL_APPROVED,
        f"{'Proposed' if status == A.PENDING else 'Requested and approved'}: {approval.title}",
        user_id=user.id,
        obligation_id=ob.id,
        actor=actor,
        data={"approval_id": approval.id, "action": action, "status": status},
        now=now,
    )
    if notify and status == A.PENDING:
        policy = policy_for(user, settings)
        base = settings.public_web_url.rstrip("/")
        content = messages.Content(
            f"Approval needed: {approval.title}",
            [
                f"CommitmentOS prepared an action for \"{ob.title}\" and will not do anything until you approve it.",
                *([rationale] if rationale else []),
            ],
            [{"label": "Review and decide", "url": f"{base}/inbox?tab=approvals"}],
        )
        notifications.queue(
            db, user=user, ob=ob, kind=NotificationKind.APPROVAL_REQUESTED, rung_key=f"APPROVAL:{approval.id}",
            content=content, policy=policy, now=now, extra_payload={"approval_id": str(approval.id)},
        )
    return approval, True


def edit(db: Session, user: User, approval: ApprovalRequest, patch: ApprovalPatch, now: datetime) -> ApprovalRequest:
    if approval.status != A.PENDING:
        raise ConflictError("Only a pending request can be edited", code="APPROVAL_NOT_PENDING")
    fields = {k: v for k, v in patch.model_dump(exclude_unset=True).items() if v is not None}
    allowed = {"subject", "body"} if approval.action_type == ApprovalAction.SEND_FOLLOW_UP else {"start_at", "end_at"}
    illegal = set(fields) - allowed
    if illegal:
        raise ValidationFailed(f"Cannot edit {sorted(illegal)} on a {approval.action_type.value} request")
    merged = {**approval.payload, **{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in fields.items()}}
    approval.payload = validate_payload(approval.action_type, merged)
    audit.record(
        db, AuditEventType.OBLIGATION_UPDATED, f"Edited the proposed action: {approval.title}",
        user_id=user.id, obligation_id=approval.obligation_id, actor=Actor.user(user),
        data={"approval_id": approval.id, "fields": sorted(fields)}, now=now,
    )
    return approval


def approve(db: Session, user: User, approval: ApprovalRequest, now: datetime, settings: Settings) -> tuple[ApprovalRequest, bool]:
    """Returns (approval, needs_n8n). Idempotent for an already-approved request."""
    if approval.status in (A.APPROVED, A.EXECUTING, A.EXECUTED):
        return approval, approval.status == A.APPROVED and approval.action_type in EXTERNAL_ACTIONS
    if approval.status != A.PENDING:
        raise ConflictError(f"This request is {approval.status.value.lower()} and can no longer be approved", code="APPROVAL_NOT_PENDING")
    approval.status = A.APPROVED
    approval.decided_at = now
    audit.record(
        db, AuditEventType.APPROVAL_APPROVED, f"Approved: {approval.title}",
        user_id=user.id, obligation_id=approval.obligation_id, actor=Actor.user(user),
        data={"approval_id": approval.id, "action": approval.action_type}, now=now,
    )
    if approval.action_type in EXTERNAL_ACTIONS:
        return approval, True
    _execute_locally(db, user, approval, now, settings)
    automation.resolve_waiting(db, now)
    return approval, False


def reject(db: Session, user: User, approval: ApprovalRequest, now: datetime) -> ApprovalRequest:
    if approval.status == A.REJECTED:
        return approval
    if approval.status != A.PENDING:
        raise ConflictError(f"This request is {approval.status.value.lower()} and can no longer be rejected", code="APPROVAL_NOT_PENDING")
    approval.status = A.REJECTED
    approval.decided_at = now
    audit.record(
        db, AuditEventType.APPROVAL_REJECTED, f"Rejected: {approval.title}",
        user_id=user.id, obligation_id=approval.obligation_id, actor=Actor.user(user),
        data={"approval_id": approval.id, "action": approval.action_type}, now=now,
    )
    automation.resolve_waiting(db, now)
    return approval


def _execute_locally(db: Session, user: User, approval: ApprovalRequest, now: datetime, settings: Settings) -> None:
    """DISMISS/COMPLETE proposals have no external side effect: the backend applies them itself."""
    ob = db.get(Obligation, approval.obligation_id)
    assert ob is not None
    if approval.action_type == ApprovalAction.DISMISS_OBLIGATION:
        ob_service.dismiss(db, user, ob, now, reason=approval.rationale or "approved proposal", actor=Actor.user(user))
    else:
        ob_service.complete(db, user, ob, now, settings, via="APPROVED_PROPOSAL", actor=Actor.user(user))
    approval.status = A.EXECUTED
    approval.executed_at = now
    approval.result = {"applied": approval.action_type.value}
    audit.record(
        db, AuditEventType.ACTION_EXECUTED, f"Applied: {approval.title}",
        user_id=user.id, obligation_id=approval.obligation_id, actor=Actor.system("approvals"),
        data={"approval_id": approval.id}, now=now,
    )


def _recipient_still_original(db: Session, approval: ApprovalRequest) -> bool:
    ob = db.get(Obligation, approval.obligation_id)
    user = db.get(User, approval.user_id)
    if ob is None or user is None:
        return False
    target = followups.follow_up_target(db, ob, user)
    return target.address is not None and str(approval.payload.get("to", "")).strip().lower() == target.address


def claim(db: Session, now: datetime, settings: Settings, limit: int = 10) -> list[ApprovalRequest]:
    """n8n takes ownership of approved external actions (APPROVED -> EXECUTING), atomically."""
    lease_cutoff = now - timedelta(seconds=settings.sending_lease_seconds)
    stmt = (
        select(ApprovalRequest)
        .where(
            ApprovalRequest.action_type.in_(list(EXTERNAL_ACTIONS)),
            or_(
                ApprovalRequest.status == A.APPROVED,
                and_(ApprovalRequest.status == A.EXECUTING, ApprovalRequest.claimed_at < lease_cutoff),
            ),
        )
        .order_by(ApprovalRequest.decided_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    claimed = []
    for approval in db.scalars(stmt):
        if approval.attempts >= settings.n8n_max_attempts:
            approval.status = A.FAILED
            approval.error = approval.error or "gave up: execution lease expired repeatedly"
            continue
        if approval.action_type == ApprovalAction.SEND_FOLLOW_UP and not _recipient_still_original(db, approval):
            # last line of defence before anything leaves the system: the stored address must still be the original sender's
            approval.status = A.FAILED
            approval.error = "Not sent: the recipient is no longer the address the original email came from."
            continue
        approval.status = A.EXECUTING
        approval.claimed_at = now
        approval.attempts += 1
        claimed.append(approval)
    db.flush()
    return claimed


def report_result(
    db: Session,
    approval: ApprovalRequest,
    *,
    ok: bool,
    result: dict[str, Any] | None,
    error: str | None,
    now: datetime,
    settings: Settings,
    run_id: uuid.UUID | None = None,
) -> None:
    if approval.status in (A.EXECUTED, A.FAILED):
        return  # duplicate report
    user = db.get(User, approval.user_id)
    ob = db.get(Obligation, approval.obligation_id)
    assert user is not None and ob is not None
    n8n = Actor.n8n()
    if not ok:
        retry = approval.attempts < settings.n8n_max_attempts
        approval.status = A.APPROVED if retry else A.FAILED
        approval.claimed_at = None
        approval.error = (error or "execution failed")[:1000]
        audit.record(
            db, AuditEventType.ACTION_FAILED,
            f"Could not complete \"{approval.title}\" (attempt {approval.attempts})" + ("; will retry" if retry else "; gave up"),
            user_id=user.id, obligation_id=ob.id, actor=n8n,
            data={"approval_id": approval.id, "error": approval.error, "attempt": approval.attempts, "will_retry": retry},
            run_id=run_id, now=now,
        )
        if not retry:
            _notify_result(db, user, ob, approval, ok=False, now=now, settings=settings)
            automation.resolve_waiting(db, now)
        return

    approval.status = A.EXECUTED
    approval.executed_at = now
    approval.result = result or {}
    approval.error = None
    if approval.action_type == ApprovalAction.CREATE_CALENDAR_EVENT:
        _apply_calendar_result(db, user, ob, approval, now)
        message = f"Calendar event created: {approval.title}"
    elif settings.mail_goes_to_local_sink:
        message = f"Placed {messages.LOCAL_INBOX_NOTE} (email delivery is not set up): {approval.title}"
    else:
        message = f"Sent: {approval.title}"
    audit.record(
        db,
        AuditEventType.CALENDAR_EVENT_CREATED if approval.action_type == ApprovalAction.CREATE_CALENDAR_EVENT else AuditEventType.ACTION_EXECUTED,
        message, user_id=user.id, obligation_id=ob.id, actor=n8n,
        data={"approval_id": approval.id, "result": result or {}}, run_id=run_id, now=now,
    )
    _notify_result(db, user, ob, approval, ok=True, now=now, settings=settings)
    automation.resolve_waiting(db, now)


def _apply_calendar_result(db: Session, user: User, ob: Obligation, approval: ApprovalRequest, now: datetime) -> None:
    payload = approval.payload
    result = approval.result or {}
    provider = CalendarProvider(result.get("provider", "LOCAL"))
    external_id = str(result.get("external_id") or f"local-{approval.id}")
    event = db.scalar(
        select(CalendarEvent).where(
            CalendarEvent.user_id == user.id, CalendarEvent.provider == provider, CalendarEvent.external_id == external_id
        )
    )
    if event is None:
        event = CalendarEvent(user_id=user.id, provider=provider, external_id=external_id, created_at=now, updated_at=now,
                              start_at=datetime.fromisoformat(payload["start_at"]), end_at=datetime.fromisoformat(payload["end_at"]),
                              title=payload["title"])
        db.add(event)
    event.obligation_id = ob.id
    event.title = payload["title"]
    event.start_at = datetime.fromisoformat(payload["start_at"])
    event.end_at = datetime.fromisoformat(payload["end_at"])
    event.timezone = payload.get("timezone", "UTC")
    event.location = payload.get("location")
    event.url = result.get("url")
    event.status = CalendarEventStatus.CONFIRMED
    event.last_verified_at = now
    event.raw = {"provider_response": result}
    if ob.status in (S.OPEN, S.ACTION_REQUIRED):
        lifecycle.transition(db, ob, S.SCHEDULED, actor=Actor.n8n(), now=now, reason="calendar event created")
    db.flush()


def _result_sentence(approval: ApprovalRequest, settings: Settings) -> str:
    if approval.action_type == ApprovalAction.SEND_FOLLOW_UP and settings.mail_goes_to_local_sink:
        return " The email was placed in the local test inbox. Nothing reached a real mailbox because email delivery is not set up."
    return " It went through."


def _notify_result(db: Session, user: User, ob: Obligation, approval: ApprovalRequest, *, ok: bool, now: datetime, settings: Settings) -> None:
    policy = policy_for(user, settings)
    base = settings.public_web_url.rstrip("/")
    outcome = _result_sentence(approval, settings) if ok else f" {approval.error or ''} You can try again from the commitment page."
    content = messages.Content(
        ("Done: " if ok else "Could not complete: ") + approval.title,
        [f"For \"{ob.title}\"." + outcome],
        [{"label": "Open commitment", "url": f"{base}/obligations/{ob.id}"}],
    )
    notifications.queue(
        db, user=user, ob=ob, kind=NotificationKind.ACTION_RESULT, rung_key=f"RESULT:{approval.id}",
        content=content, policy=policy, now=now, external=False,
    )

