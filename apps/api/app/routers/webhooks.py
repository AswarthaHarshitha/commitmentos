"""POST /api/webhooks/n8n - how n8n reports back what it did.

Authenticated with the shared inbound secret (X-Webhook-Secret). Every handler is idempotent:
n8n retries HTTP calls, so a duplicate event must be a harmless no-op.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import get_now, require_n8n
from app.enums import TERMINAL_STATUSES, ApprovalAction, RunStatus
from app.errors import NotFoundError
from app.models import ApprovalRequest, AutomationRun, Notification, Obligation, User
from app.schemas.webhook import (
    ApprovalReport,
    MessageExtracted,
    NotificationReport,
    ProposalCreated,
    RunFinished,
    RunStarted,
    WebhookEvent,
)
from app.services import approvals, automation, ingestion, notifications

log = logging.getLogger("commitmentos.webhooks")
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"], dependencies=[Depends(require_n8n)])


def _user_id_by_email(db: Session, email: str | None) -> uuid.UUID | None:
    if not email:
        return None
    return db.scalar(select(User.id).where(User.email == email.strip().lower()))


def _run_id(db: Session, n8n_execution_id: str | None) -> uuid.UUID | None:
    if not n8n_execution_id:
        return None
    run = automation.find_by_execution(db, n8n_execution_id)
    return run.id if run else None


def _on_run_started(db: Session, ev: RunStarted, now: datetime, settings: Settings) -> dict[str, Any]:
    run = automation.start_run(
        db,
        workflow_key=ev.workflow_key,
        now=now,
        n8n_execution_id=ev.n8n_execution_id,
        n8n_workflow_id=ev.n8n_workflow_id,
        workflow_display_name=ev.workflow_name,
        trigger=ev.trigger,
        user_id=_user_id_by_email(db, ev.user_email),
        obligation_id=ev.obligation_id,
        correlation_id=ev.correlation_id,
        status=RunStatus(ev.status),
    )
    return {"run_id": str(run.id)}


def _on_run_finished(db: Session, ev: RunFinished, now: datetime, settings: Settings) -> dict[str, Any]:
    run = automation.find_by_execution(db, ev.n8n_execution_id)
    if run is None:  # the started event never arrived (n8n crashed / network blip): create the row now
        run = automation.start_run(
            db,
            workflow_key=ev.workflow_key or "unknown",
            now=now,
            n8n_execution_id=ev.n8n_execution_id,
            n8n_workflow_id=ev.n8n_workflow_id,
            workflow_display_name=ev.workflow_name,
            trigger=ev.trigger or "WEBHOOK",
            user_id=_user_id_by_email(db, ev.user_email),
        )
    status = RunStatus.FAILED if ev.event == "run.failed" else (ev.status or RunStatus.SUCCESS)
    automation.finish_run(
        db,
        run,
        status=status,
        now=now,
        result=ev.result,
        error=ev.error,
        error_node=ev.error_node,
        duration_ms=ev.duration_ms,
        user_id=_user_id_by_email(db, ev.user_email),
        obligation_id=ev.obligation_id,
    )
    if status == RunStatus.WAITING:
        automation.resolve_waiting(db, now)  # the human may already have decided while n8n was still wrapping up
    if status == RunStatus.FAILED:
        log.warning("n8n run failed: workflow=%s node=%s", run.workflow_key, ev.error_node)
    return {"run_id": str(run.id), "status": run.status.value}


def _on_notification(db: Session, ev: NotificationReport, now: datetime, settings: Settings) -> dict[str, Any]:
    note = db.scalar(select(Notification).where(Notification.id == ev.notification_id).with_for_update())
    if note is None:
        raise NotFoundError("Notification not found")
    run_id = _run_id(db, ev.n8n_execution_id)
    if ev.event == "notification.sent":
        notifications.mark_sent(db, note, now, run_id)
    else:
        notifications.mark_failed(db, note, ev.error or "delivery failed", now, settings, run_id)
    return {"status": note.status.value}


def _on_approval(db: Session, ev: ApprovalReport, now: datetime, settings: Settings) -> dict[str, Any]:
    approval = db.scalar(select(ApprovalRequest).where(ApprovalRequest.id == ev.approval_id).with_for_update())
    if approval is None:
        raise NotFoundError("Approval request not found")
    approvals.report_result(
        db, approval, ok=ev.event == "approval.executed", result=ev.result, error=ev.error,
        now=now, settings=settings, run_id=_run_id(db, ev.n8n_execution_id),
    )
    return {"status": approval.status.value}


def _on_proposal(db: Session, ev: ProposalCreated, now: datetime, settings: Settings) -> dict[str, Any]:
    ob = db.get(Obligation, ev.obligation_id)
    if ob is None:
        raise NotFoundError("Obligation not found")
    user = db.get(User, ob.user_id)
    assert user is not None
    if ob.status in TERMINAL_STATUSES:  # a workflow acting on stale data: a no-op, not an error n8n would retry
        return {"approval_id": None, "created": False, "reason": f"the obligation is already {ob.status.value.lower()}"}
    approval, created = approvals.propose(
        db, user=user, ob=ob, action=ApprovalAction(ev.action_type), title=ev.title, payload=ev.payload,
        proposed_by=ev.proposed_by, rationale=ev.rationale, now=now, settings=settings, run_id=_run_id(db, ev.n8n_execution_id),
    )
    return {"approval_id": str(approval.id), "created": created}


def _on_message_extracted(db: Session, ev: MessageExtracted, now: datetime, settings: Settings) -> dict[str, Any]:
    run_id = _run_id(db, ev.n8n_execution_id)
    result = ingestion.handle_extracted_message(db, ev, now, settings, run_id)
    if run_id is not None:
        run = db.get(AutomationRun, run_id)
        user_id = _user_id_by_email(db, ev.user_email)
        obligation_id = uuid.UUID(result["obligation_id"]) if result.get("obligation_id") else None
        if run is not None:
            automation.link_run(run, user_id=user_id, obligation_id=obligation_id)
    return result


_HANDLERS = {
    RunStarted: _on_run_started,
    RunFinished: _on_run_finished,
    NotificationReport: _on_notification,
    ApprovalReport: _on_approval,
    ProposalCreated: _on_proposal,
    MessageExtracted: _on_message_extracted,
}


@router.post("/n8n", summary="Events reported by n8n workflows")
def n8n_webhook(
    event: WebhookEvent,
    db: Session = Depends(get_db),
    now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    result = _HANDLERS[type(event)](db, event, now, settings)
    db.commit()
    return {"ok": True, "event": event.event, **result}

