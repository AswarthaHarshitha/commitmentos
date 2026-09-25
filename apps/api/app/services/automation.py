"""Automation run tracking: what makes n8n's work visible.

n8n workflows report ``run.started`` / ``run.finished`` (and a global error workflow reports
``run.failed``) with their own execution id. Everything is idempotent on ``n8n_execution_id`` so
n8n's own HTTP retries cannot create duplicate rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.enums import ApprovalStatus, RunStatus
from app.models import ApprovalRequest, AutomationRun

WORKFLOWS: dict[str, str] = {
    "incoming-detection": "Commitment Detection",
    "deadline-monitor": "Deadline Monitor",
    "calendar-sync": "Calendar Synchronization",
    "approved-actions": "Approved Actions",
    "completion-detection": "Completion Detection",
    "follow-up-assistant": "Follow-up Assistant",
    "notification-dispatcher": "Notification Dispatcher",
    "api-dispatch": "API to n8n dispatch",
}
_FINAL = {RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.SKIPPED, RunStatus.PARTIAL}
_OPEN_APPROVALS = (ApprovalStatus.PENDING, ApprovalStatus.APPROVED, ApprovalStatus.EXECUTING)


def workflow_name(key: str, fallback: str | None = None) -> str:
    return WORKFLOWS.get(key) or fallback or key


def find_by_execution(db: Session, n8n_execution_id: str) -> AutomationRun | None:
    return db.scalar(select(AutomationRun).where(AutomationRun.n8n_execution_id == n8n_execution_id))


def start_run(
    db: Session,
    *,
    workflow_key: str,
    now: datetime,
    n8n_execution_id: str | None = None,
    n8n_workflow_id: str | None = None,
    workflow_display_name: str | None = None,
    trigger: str = "WEBHOOK",
    user_id: uuid.UUID | None = None,
    obligation_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
    status: RunStatus = RunStatus.RUNNING,
) -> AutomationRun:
    if n8n_execution_id:
        existing = find_by_execution(db, n8n_execution_id)
        if existing is not None:
            return existing  # duplicate run.started from an n8n retry
    run = AutomationRun(
        workflow_key=workflow_key,
        workflow_name=workflow_name(workflow_key, workflow_display_name),
        n8n_workflow_id=n8n_workflow_id,
        n8n_execution_id=n8n_execution_id,
        trigger=trigger,
        user_id=user_id,
        obligation_id=obligation_id,
        correlation_id=correlation_id,
        status=status,
        started_at=now,
        created_at=now,
    )
    db.add(run)
    db.flush()
    return run


def finish_run(
    db: Session,
    run: AutomationRun,
    *,
    status: RunStatus,
    now: datetime,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    error_node: str | None = None,
    duration_ms: int | None = None,
    user_id: uuid.UUID | None = None,
    obligation_id: uuid.UUID | None = None,
) -> AutomationRun:
    if run.status in _FINAL and run.finished_at is not None:
        return run  # duplicate run.finished
    run.status = status
    run.finished_at = now if status in _FINAL else None
    if status in _FINAL:
        run.duration_ms = duration_ms if duration_ms is not None else max(0, int((now - run.started_at).total_seconds() * 1000))
    elif duration_ms is not None:
        run.duration_ms = duration_ms  # WAITING: how long n8n itself worked; the human's decision time is not "duration"
    if result is not None:
        run.result = result
    run.error = (error or None) and error[:2000]
    run.error_node = error_node
    if user_id and run.user_id is None:
        run.user_id = user_id
    if obligation_id and run.obligation_id is None:
        run.obligation_id = obligation_id
    db.flush()
    return run


def record_backend_failure(
    db: Session, *, workflow_key: str, now: datetime, error: str, user_id: uuid.UUID | None, trigger: str = "API",
    obligation_id: uuid.UUID | None = None, correlation_id: str | None = None,
) -> AutomationRun:
    """The API tried to reach n8n and could not: make that visible in the automation history."""
    run = start_run(
        db, workflow_key=workflow_key, now=now, trigger=trigger, user_id=user_id,
        obligation_id=obligation_id, correlation_id=correlation_id,
    )
    return finish_run(db, run, status=RunStatus.FAILED, now=now, error=error, duration_ms=0)


def resolve_waiting(db: Session, now: datetime) -> int:
    """Close WAITING runs whose approvals have all been decided; returns how many were closed.

    A workflow that proposes an action for a human ends WAITING ("paused for a human decision"). Once every
    approval it proposed is decided (executed, rejected, expired, cancelled or failed) the run has nothing
    left to wait for. Called wherever an approval leaves its open states, and again when the run itself reports
    WAITING (the human may have been quicker than n8n's final report).
    """
    waiting = db.scalars(select(AutomationRun).where(AutomationRun.status == RunStatus.WAITING).with_for_update()).all()
    closed = 0
    for run in waiting:
        counts = {
            status: n
            for status, n in db.execute(
                select(ApprovalRequest.status, func.count()).where(ApprovalRequest.automation_run_id == run.id).group_by(ApprovalRequest.status)
            ).all()
        }
        if not counts or any(counts.get(s, 0) for s in _OPEN_APPROVALS):
            continue  # nothing linked (waiting on something else) or a decision is still outstanding
        failed = counts.get(ApprovalStatus.FAILED, 0)
        executed = counts.get(ApprovalStatus.EXECUTED, 0)
        run.status = RunStatus.SUCCESS if not failed else (RunStatus.PARTIAL if executed else RunStatus.FAILED)
        run.finished_at = now
        if failed:
            run.error = "an approved action failed after its retries"
        run.result = {**(run.result or {}), "resolved": {s.value.lower(): n for s, n in sorted(counts.items(), key=lambda kv: kv[0].value)}}
        closed += 1
    if closed:
        db.flush()
    return closed


def link_run(run: AutomationRun, *, user_id: uuid.UUID | None, obligation_id: uuid.UUID | None) -> None:
    """Attach the run to the user / obligation it produced (drives 'automation history' on the detail page)."""
    if user_id and run.user_id is None:
        run.user_id = user_id
    if obligation_id and run.obligation_id is None:
        run.obligation_id = obligation_id


def summary(db: Session, since: datetime) -> list[dict[str, Any]]:
    """Per-workflow health over a window (drives the Automations console header)."""
    rows = db.execute(
        select(
            AutomationRun.workflow_key,
            func.count().label("runs"),
            func.sum(case((AutomationRun.status == RunStatus.SUCCESS, 1), else_=0)).label("ok"),
            func.sum(case((AutomationRun.status == RunStatus.FAILED, 1), else_=0)).label("failed"),
            func.sum(case((AutomationRun.status == RunStatus.WAITING, 1), else_=0)).label("waiting"),
            func.avg(AutomationRun.duration_ms).label("avg_ms"),
            func.max(AutomationRun.started_at).label("last_started"),
        )
        .where(AutomationRun.started_at >= since)
        .group_by(AutomationRun.workflow_key)
    ).all()
    by_key = {r.workflow_key: r for r in rows}
    out = []
    for key, name in WORKFLOWS.items():
        r = by_key.get(key)
        out.append(
            {
                "workflow_key": key,
                "workflow_name": name,
                "runs": int(r.runs) if r else 0,
                "succeeded": int(r.ok or 0) if r else 0,
                "failed": int(r.failed or 0) if r else 0,
                "waiting": int(r.waiting or 0) if r else 0,
                "avg_duration_ms": int(r.avg_ms) if r and r.avg_ms is not None else None,
                "last_started_at": r.last_started if r else None,
            }
        )
    return out


def default_window(now: datetime) -> datetime:
    return now - timedelta(hours=24)
