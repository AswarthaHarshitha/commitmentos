"""Import an email a person pasted in: hand it to n8n's detection workflow, and report what became of it.

n8n stays the orchestrator - the API only builds the same normalised message a Gmail poll would produce and asks the
`commitmentos-ingest` workflow to run it. What comes back is read from the database (the idempotency ledger), not from n8n's
HTTP reply, so the answer is the same whichever way the workflow finished.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime
from email.utils import parseaddr
from typing import Any

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import clock
from app.db import get_sessionmaker
from app.enums import RunStatus, SourceDisposition, SourceType
from app.models import AutomationRun, DetectionCandidate, Source, User
from app.schemas.messages import ImportEmail, ImportOutcome
from app.services import automation
from app.services.n8n_client import N8nClient

log = logging.getLogger("commitmentos.import")

INGEST_WEBHOOK = "commitmentos-ingest"
# The ingest webhook answers when the whole workflow has finished, model call included, so the wait is long and is not retried:
# a retry after a timeout would start a second execution of a message that is still being read.
INGEST_TIMEOUT_SECONDS = 240.0

_EXTRACTION_FAILURES = {
    "LLM_QUOTA_EXHAUSTED": "The language model's usage limit has been reached. Import the email again later.",
    "LLM_UNAVAILABLE": "The language model did not answer. Import the email again in a moment.",
    "LLM_TIMEOUT": "The language model took too long. Import the email again in a moment.",
    "INVALID_OUTPUT": "The language model's answer could not be used. Import the email again, or add the commitment by hand.",
}


def external_id(user: User, data: ImportEmail) -> str:
    """The same email pasted twice is the same message: identical text gives an identical id, so it is recognised, not re-read."""
    digest = hashlib.sha256("\n".join([str(user.id), data.subject or "", data.sender or "", data.body]).encode()).hexdigest()
    return f"import-{digest[:40]}"


def split_sender(sender: str | None) -> tuple[str | None, str | None]:
    """'Dana Whitfield <dana@example.org>' -> (name, address). Anything that is not a valid address is kept as a name only."""
    if not sender:
        return None, None
    name, address = parseaddr(sender)
    if address:
        try:
            return (name or None), validate_email(address, check_deliverability=False).normalized
        except EmailNotValidError:
            pass
    return (sender.strip()[:200] or None), None


def build_payload(user: User, data: ImportEmail, ext_id: str, received_at: datetime) -> dict[str, Any]:
    name, address = split_sender(data.sender)
    return {
        "user_email": user.email,
        "message": {
            "source_type": SourceType.IMPORTED.value,
            "external_id": ext_id,
            "sender_email": address,
            "sender_name": name,
            "subject": data.subject,
            "body": data.body,
            "received_at": received_at.isoformat(),
            "direction": "INBOUND",
        },
    }


def find_source(db: Session, user: User, ext_id: str) -> Source | None:
    return db.scalar(select(Source).where(Source.user_id == user.id, Source.source_type == SourceType.IMPORTED, Source.external_id == ext_id))


def dispatch(payload: dict[str, Any], ext_id: str, user_id: uuid.UUID, n8n: N8nClient) -> None:
    """Background task: run the detection workflow. A hand-off that fails is written to the automation history, where it is visible."""
    result = n8n.trigger(INGEST_WEBHOOK, payload, timeout=INGEST_TIMEOUT_SECONDS, attempts=1)
    if result.ok:
        return
    with get_sessionmaker()() as db:
        automation.record_backend_failure(
            db,
            workflow_key="api-dispatch",
            now=clock.now(),
            user_id=user_id,
            correlation_id=ext_id,
            error=f"The email could not be handed to the automation engine ({result.error}). Import it again once the engine is running.",
        )
        db.commit()


def outcome(db: Session, user: User, ext_id: str, since: datetime | None = None) -> ImportOutcome:
    """What became of an imported email. `since` is when the import was requested: a source that was last touched before then
    is the result of an EARLIER attempt (a failed read being retried), so it does not answer this one."""
    source = find_source(db, user, ext_id)
    if source is not None and (since is None or source.updated_at >= since):
        obligation = source.obligation
        title = obligation.title if obligation is not None else None
        if title is None and source.disposition == SourceDisposition.CANDIDATE:
            title = db.scalar(select(DetectionCandidate.title).where(DetectionCandidate.source_id == source.id))
        detail = None
        if source.disposition == SourceDisposition.EXTRACTION_FAILED:
            status = (source.extraction or {}).get("error_status", "")
            detail = _EXTRACTION_FAILURES.get(status, "The email could not be read. It is recorded under Automations.")
        return ImportOutcome(
            external_id=ext_id,
            status="failed" if source.disposition == SourceDisposition.EXTRACTION_FAILED else "done",
            disposition=source.disposition,
            obligation_id=source.obligation_id,
            obligation_status=obligation.status if obligation is not None else None,
            title=title,
            detail=detail,
        )
    failed = db.scalar(
        select(AutomationRun)
        .where(AutomationRun.user_id == user.id, AutomationRun.correlation_id == ext_id, AutomationRun.status == RunStatus.FAILED)
        .where(AutomationRun.started_at >= since if since is not None else True)
        .order_by(AutomationRun.started_at.desc())
        .limit(1)
    )
    if failed is not None:
        return ImportOutcome(external_id=ext_id, status="failed", detail=failed.error)
    return ImportOutcome(external_id=ext_id, status="processing")
