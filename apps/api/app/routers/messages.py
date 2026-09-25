"""Bring an email in by hand: paste it, and it is read like any other message."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import enforce, get_current_user, get_now, limiter
from app.enums import SourceDisposition
from app.errors import ValidationFailed
from app.models import User
from app.schemas.messages import ImportEmail, ImportOutcome
from app.services import importing
from app.services.n8n_client import N8nClient, get_n8n_client

router = APIRouter(prefix="/api/messages", tags=["messages"])


@router.post("/import", response_model=ImportOutcome, status_code=202, summary="Import an email you pasted in")
def import_email(
    body: ImportEmail,
    background: BackgroundTasks,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
    n8n: N8nClient = Depends(get_n8n_client),
) -> ImportOutcome:
    """Queues the email for the detection workflow and returns at once (202); poll `GET /import/{external_id}?since=...` for the result.

    The same text imported twice is recognised and answered from the record (200) without reading it again - unless the first
    attempt failed, in which case it is read again."""
    enforce(limiter("import", 20, 60), str(user.id))
    received_at = body.received_at or now
    if received_at > now + timedelta(minutes=5):
        raise ValidationFailed("An email cannot have arrived in the future")
    ext_id = importing.external_id(user, body)
    existing = importing.find_source(db, user, ext_id)
    if existing is not None and existing.disposition != SourceDisposition.EXTRACTION_FAILED:
        response.status_code = 200
        return importing.outcome(db, user, ext_id)
    background.add_task(importing.dispatch, importing.build_payload(user, body, ext_id, received_at), ext_id, user.id, n8n)
    return ImportOutcome(external_id=ext_id, status="processing", requested_at=now)


@router.get("/import/{external_id}", response_model=ImportOutcome, summary="Where an imported email has got to")
def import_outcome(
    external_id: str,
    since: datetime | None = Query(default=None, description="The `requested_at` returned by the import; ignores results from earlier attempts"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ImportOutcome:
    if since is not None and since.tzinfo is None:
        raise ValidationFailed("since must include a UTC offset")
    return importing.outcome(db, user, external_id[:512], since)
