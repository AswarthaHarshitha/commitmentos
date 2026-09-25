"""Low-confidence detections. Kept for review; promoting one creates a NEEDS_REVIEW obligation."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import get_current_user, get_now
from app.enums import CandidateStatus
from app.errors import NotFoundError
from app.models import DetectionCandidate, Source, User
from app.schemas.misc import CandidateOut
from app.schemas.obligation import ObligationOut
from app.services import ingestion

router = APIRouter(prefix="/api/candidates", tags=["candidates"])


def _out(c: DetectionCandidate, src: Source | None) -> CandidateOut:
    fields = c.extraction.get("obligation_fields", {}) if c.extraction else {}
    due = fields.get("due_at")
    return CandidateOut(
        id=c.id, title=c.title, action=c.action, obligation_type=c.obligation_type, confidence=c.confidence, reason=c.reason,
        status=c.status, explanation=(c.extraction or {}).get("explanation"), deadline_text=(c.extraction or {}).get("deadline_text"),
        due_at=datetime.fromisoformat(due) if due else None,
        sender_email=src.sender_email if src else None, subject=src.subject if src else None, excerpt=src.excerpt if src else None,
        received_at=src.received_at if src else None, created_at=c.created_at, promoted_obligation_id=c.promoted_obligation_id,
    )


def _owned(db: Session, user: User, candidate_id: uuid.UUID) -> DetectionCandidate:
    c = db.scalar(select(DetectionCandidate).where(DetectionCandidate.id == candidate_id, DetectionCandidate.user_id == user.id).with_for_update())
    if c is None:
        raise NotFoundError("Candidate not found")
    return c


@router.get("", response_model=list[CandidateOut])
def list_candidates(
    status: CandidateStatus = Query(CandidateStatus.PENDING),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[CandidateOut]:
    rows = db.scalars(
        select(DetectionCandidate).where(DetectionCandidate.user_id == user.id, DetectionCandidate.status == status)
        .order_by(DetectionCandidate.created_at.desc()).limit(limit)
    ).all()
    sources = {s.id: s for s in db.scalars(select(Source).where(Source.id.in_([c.source_id for c in rows])))} if rows else {}
    return [_out(c, sources.get(c.source_id)) for c in rows]


@router.post("/{candidate_id}/promote", response_model=ObligationOut, status_code=201)
def promote(
    candidate_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
    settings: Settings = Depends(get_settings),
) -> ObligationOut:
    ob = ingestion.promote_candidate(db, user, _owned(db, user, candidate_id), now, settings)
    db.commit()
    return ObligationOut.model_validate(ob)


@router.post("/{candidate_id}/discard", status_code=204)
def discard(
    candidate_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    now: datetime = Depends(get_now),
) -> None:
    ingestion.discard_candidate(db, user, _owned(db, user, candidate_id), now)
    db.commit()
