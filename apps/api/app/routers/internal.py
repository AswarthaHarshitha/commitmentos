"""/api/internal/* - endpoints only n8n may call (shared inbound secret).

These are the "do the deterministic work" halves of the workflows: n8n decides *when* and *where*
(triggers, retries, delivery), the API decides *what* (which reminders are due, which notification
to send, which approved action to run).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import enforce, get_now, limiter, require_n8n
from app.enums import ApprovalStatus, SourceType
from app.errors import NotFoundError, ValidationFailed
from app.models import ApprovalRequest, Obligation, User
from app.services import approvals, audit, calendar_sync, completion, followups, ingestion, monitor, notifications, testclock
from app.services.extraction.llm import LLMClient, get_llm_client
from app.services.extraction.schema import MessageEnvelope
from app.services.extraction.service import ExtractionStatus, extract_message

router = APIRouter(prefix="/api/internal", tags=["internal (n8n only)"], dependencies=[Depends(require_n8n)])


class LimitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=25, ge=1, le=100)


@router.post("/monitor/tick", summary="Evaluate deadlines: reminders, overdue, escalation")
def monitor_tick(
    db: Session = Depends(get_db), now: datetime = Depends(get_now), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    summary = monitor.run_tick(db, now, settings)
    db.commit()
    return {"now": now, **summary.as_dict()}


@router.post("/notifications/claim", summary="Hand pending EMAIL/TELEGRAM notifications to the deliverer")
def claim_notifications(
    body: LimitRequest | None = None,
    db: Session = Depends(get_db),
    now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    claimed = notifications.claim_pending(db, now, settings, (body or LimitRequest()).limit)
    items = [notifications.delivery_view(n, u, settings) for n, u in claimed]
    db.commit()
    return {"items": items, "count": len(items)}


@router.post("/approvals/claim", summary="Hand approved external actions to n8n (APPROVED -> EXECUTING)")
def claim_approvals(
    body: LimitRequest | None = None,
    db: Session = Depends(get_db),
    now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    claimed = approvals.claim(db, now, settings, (body or LimitRequest()).limit)
    items = []
    for a in claimed:
        user = db.get(User, a.user_id)
        ob = db.get(Obligation, a.obligation_id)
        assert user is not None and ob is not None
        items.append(_approval_view(a, user, ob, settings))
    db.commit()
    return {"items": items, "count": len(items)}


@router.get("/approvals/{approval_id}", summary="Fetch one approved action to execute")
def get_approval_for_execution(
    approval_id: str,
    db: Session = Depends(get_db),
    now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Push path: the webhook carries only an id. The workflow claims the row atomically here, so if
    the scheduled pull already took it, this returns ``claimed: false`` and the workflow stops."""
    try:
        key = uuid.UUID(approval_id)
    except ValueError:
        return {"claimed": False, "reason": "invalid id"}
    a = db.scalar(select(ApprovalRequest).where(ApprovalRequest.id == key).with_for_update())
    if a is None:
        return {"claimed": False, "reason": "not found"}
    if a.status != ApprovalStatus.APPROVED:
        return {"claimed": False, "reason": f"status is {a.status.value}"}
    if a.attempts >= settings.n8n_max_attempts:
        a.status = ApprovalStatus.FAILED
        a.error = a.error or "gave up: too many attempts"
        db.commit()
        return {"claimed": False, "reason": "too many attempts"}
    a.status = ApprovalStatus.EXECUTING
    a.claimed_at = now
    a.attempts += 1
    user = db.get(User, a.user_id)
    ob = db.get(Obligation, a.obligation_id)
    assert user is not None and ob is not None
    view = _approval_view(a, user, ob, settings)
    db.commit()
    return {"claimed": True, "item": view}


def _approval_view(a: Any, user: User, ob: Obligation, settings: Settings) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "action_type": a.action_type.value,
        "payload": a.payload,
        "attempt": a.attempts,
        "user_email": user.email,
        "from_email": settings.notify_from_email,
        "timezone": user.timezone,
        "obligation_id": str(ob.id),
        "obligation_title": ob.title,
        "calendar_provider": settings.calendar_provider,
    }


class _UserMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_email: EmailStr
    message: MessageEnvelope


class SeenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_email: EmailStr
    source_type: SourceType = SourceType.GMAIL
    external_id: str = Field(min_length=1, max_length=512)


def _user_by_email(db: Session, email: str) -> User:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        raise NotFoundError("No CommitmentOS account for this mailbox owner")
    return user


@router.post("/messages/check", summary="Has this message already been processed? (asked BEFORE paying for an LLM call)")
def messages_check(body: SeenRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return ingestion.check_seen(db, _user_by_email(db, body.user_email), body.source_type, body.external_id)


@router.post("/extract", summary="AI extraction + validation + deterministic analysis for one message (no persistence)")
def extract(
    body: _UserMessage,
    db: Session = Depends(get_db),
    now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
    llm: LLMClient = Depends(get_llm_client),
) -> JSONResponse:
    """Transient LLM failures return 503 (so n8n's retry policy applies); every other outcome is 200 with a
    ``status`` n8n can branch on - including ``LLM_QUOTA_EXHAUSTED`` (``retryable: false``: retrying within
    seconds cannot help, so the workflow must not spin on it). The reply is a *proposal*: the commit step
    re-validates it."""
    user = _user_by_email(db, body.user_email)
    enforce(limiter("extract", 120, 60), user.email)  # a misconfigured poll loop must not run up an LLM bill
    outcome = extract_message(body.message, user, settings, llm, now)
    payload = audit.jsonable(outcome.as_response())
    if outcome.status in (ExtractionStatus.LLM_UNAVAILABLE, ExtractionStatus.LLM_TIMEOUT):
        return JSONResponse(status_code=503, content=payload, headers={"Retry-After": "10"})
    return JSONResponse(content=payload)


# ------------------------------------------------------------------------------------ scans that end in proposals
@router.post("/calendar/scan", summary="Commitments that deserve a calendar event (proposals only - nothing is created)")
def calendar_scan(
    body: LimitRequest | None = None, db: Session = Depends(get_db), now: datetime = Depends(get_now), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    items = [p.as_dict() for p in calendar_sync.scan(db, now, settings, (body or LimitRequest()).limit)]
    return {"items": items, "count": len(items)}


@router.post("/followups/scan", summary="Commitments that warrant a follow-up email draft (proposals only - nothing is sent)")
def followups_scan(
    body: LimitRequest | None = None, db: Session = Depends(get_db), now: datetime = Depends(get_now), settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    items = [p.as_dict() for p in followups.scan(db, now, settings, (body or LimitRequest()).limit)]
    return {"items": items, "count": len(items)}


@router.post("/completion/check", summary="Does this message show that a tracked commitment has been fulfilled? (proposals only)")
def completion_check(
    body: _UserMessage,
    db: Session = Depends(get_db),
    now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
    llm: LLMClient = Depends(get_llm_client),
) -> JSONResponse:
    """Same failure policy as ``/extract``: transient LLM failures are 503 (n8n retries), everything else is 200 with a
    ``status``. With no plausible candidate the model is never called (``llm_called: false``)."""
    user = _user_by_email(db, body.user_email)
    enforce(limiter("completion", 120, 60), user.email)
    outcome = completion.check_completion(db, user, body.message, settings, llm, now)
    payload = audit.jsonable(outcome.as_response())
    if outcome.status in (ExtractionStatus.LLM_UNAVAILABLE, ExtractionStatus.LLM_TIMEOUT):
        return JSONResponse(status_code=503, content=payload, headers={"Retry-After": "10"})
    return JSONResponse(content=payload)


# ------------------------------------------------------------------------------------ test clock
class ClockCommand(BaseModel):
    """Exactly one of: advance the virtual clock, jump it to a moment, or hand it back to real time."""

    model_config = ConfigDict(extra="forbid")
    advance_seconds: int | None = Field(default=None, ge=1, le=int(testclock.MAX_ADVANCE.total_seconds()))
    set_to: datetime | None = None
    reset: bool = False

    @model_validator(mode="after")
    def _exactly_one(self) -> ClockCommand:
        if sum([self.advance_seconds is not None, self.set_to is not None, self.reset]) != 1:
            raise ValueError("send exactly one of advance_seconds, set_to, reset")
        return self


def _require_test_clock(settings: Settings) -> None:
    if not settings.test_clock:
        raise NotFoundError("The test clock is not enabled")  # 404: a real instance does not advertise time travel


@router.get("/test-clock", summary="The virtual clock (test stack only)")
def get_clock(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    _require_test_clock(settings)
    return testclock.state()


@router.post("/test-clock", summary="Advance, set or reset the virtual clock (test stack only)")
def command_clock(body: ClockCommand, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    _require_test_clock(settings)
    if body.reset:
        result = testclock.reset(db)
    elif body.advance_seconds is not None:
        result = testclock.advance(db, timedelta(seconds=body.advance_seconds))
    else:
        assert body.set_to is not None
        try:
            result = testclock.set_to(db, body.set_to)
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc
    db.commit()
    return result
