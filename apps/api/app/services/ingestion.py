"""Ingestion: turn a validated extraction into database state - exactly once.

Called when n8n reports ``message.extracted``. It never trusts the caller's conclusions: the extraction is
re-validated and re-analysed here (grounding, deadline resolution, thresholds), so a buggy or tampered
workflow payload cannot create an obligation the rules would not have created.

Privacy: audit rows are immutable, so they carry ids and structured metadata only - never subjects, quotes
or message bodies. Messages that are not obligations leave no content behind at all.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import (
    TERMINAL_STATUSES,
    AuditEventType,
    CandidateStatus,
    DuePrecision,
    NotificationKind,
    ObligationType,
    Priority,
    Recurrence,
    SourceDisposition,
    SourceType,
)
from app.enums import (
    ObligationStatus as S,
)
from app.errors import ConflictError, NotFoundError, ValidationFailed
from app.models import DetectionCandidate, Obligation, Source, User
from app.schemas.webhook import MessageExtracted
from app.services import audit, dedup, lifecycle, messages, monitor, notifications
from app.services.audit import Actor
from app.services.extraction.schema import MessageEnvelope
from app.services.extraction.service import Analysis, analyse, sanitize_text
from app.services.extraction.validate import InvalidOutput, validate_extraction
from app.services.reminders import policy_for
from app.services.timeutil import get_zone, local_date

log = logging.getLogger("commitmentos.ingestion")

DUPLICATE_WINDOW = timedelta(days=90)
EXTERNAL_NOTIFY_WITHIN = timedelta(hours=72)


@dataclass
class DuplicateMatch:
    obligation: Obligation
    score: float
    reasons: tuple[str, ...]
    deadline_conflict: bool


# ------------------------------------------------------------------------------------ idempotency
def _find_source(db: Session, user_id: uuid.UUID, message: MessageEnvelope, *, lock: bool = False) -> Source | None:
    stmt = select(Source).where(
        Source.user_id == user_id, Source.source_type == message.source_type, Source.external_id == message.external_id
    )
    return db.scalar(stmt.with_for_update() if lock else stmt)


def check_seen(db: Session, user: User, source_type: SourceType, external_id: str) -> dict[str, Any]:
    """Cheap pre-flight for n8n, *before* it pays for an LLM call. A previously FAILED message may be retried."""
    src = db.scalar(select(Source).where(Source.user_id == user.id, Source.source_type == source_type, Source.external_id == external_id))
    if src is None or src.disposition == SourceDisposition.EXTRACTION_FAILED:
        return {"seen": False}
    return {"seen": True, "disposition": src.disposition.value, "obligation_id": str(src.obligation_id) if src.obligation_id else None}


# ------------------------------------------------------------------------------------ deduplication
def find_duplicate(db: Session, user: User, message: MessageEnvelope, analysis: Analysis, settings: Settings, now: datetime) -> DuplicateMatch | None:
    ext, res = analysis.extraction, analysis.resolution
    tz = get_zone(user.timezone, settings.default_timezone)
    fingerprint = dedup.fingerprint(ext.title or "", local_date(res.due_at, tz) if res.due_at else None)

    exact = db.scalar(
        select(Obligation).where(Obligation.user_id == user.id, Obligation.fingerprint == fingerprint).order_by(Obligation.created_at.desc()).limit(1)
    )
    if exact is not None:
        return DuplicateMatch(exact, 1.0, ("same normalised title and due date",), False)

    recent = db.scalars(
        select(Obligation)
        .where(Obligation.user_id == user.id, Obligation.created_at >= now - DUPLICATE_WINDOW)
        .order_by(Obligation.created_at.desc())
        .limit(300)
    ).all()
    if not recent:
        return None
    threads: dict[uuid.UUID, set[str]] = {}
    senders: dict[uuid.UUID, str | None] = {}
    for oid, thread, sender in db.execute(
        select(Source.obligation_id, Source.thread_id, Source.sender_email).where(Source.obligation_id.in_([o.id for o in recent]))
    ):
        if thread:
            threads.setdefault(oid, set()).add(thread)
        senders.setdefault(oid, sender)
    best: DuplicateMatch | None = None
    for ob in recent:
        m = dedup.score_candidate(
            new_title=ext.title or "", new_due=res.due_at, new_sender=message.sender_email, new_thread=message.thread_id,
            existing_title=ob.title, existing_due=ob.due_at, existing_sender=senders.get(ob.id) or ob.counterparty_email,
            existing_threads=threads.get(ob.id, set()),
        )
        if m.score >= dedup.DUPLICATE_THRESHOLD and (best is None or m.score > best.score):
            best = DuplicateMatch(ob, m.score, m.reasons, m.deadline_conflict)
    return best


# ------------------------------------------------------------------------------------ helpers
def _excerpt(message: MessageEnvelope, analysis: Analysis, body: str) -> str:
    """A short window of the original text so the user can verify *why* something was detected."""
    quote = analysis.extraction.source_context
    if quote:
        idx = body.lower().find(quote.lower()[:60])
        if idx >= 0:
            start = max(0, idx - 150)
            end = min(len(body), idx + len(quote) + 250)
            return ("…" if start else "") + body[start:end].strip() + ("…" if end < len(body) else "")
    return body[:500].strip() + ("…" if len(body) > 500 else "")


def _join(*parts: str | None) -> str | None:
    """Reasons from several sources as one sentence-like line: no doubled punctuation, no repeats, each starting with a capital."""
    seen: list[str] = []
    for p in parts:
        text = (p or "").strip().rstrip(".;, ").strip()
        text = text[:1].upper() + text[1:]
        if text and text.lower() not in (s.lower() for s in seen):
            seen.append(text)
    return "; ".join(seen) or None


def _origin_address(hinted: str | None, message: MessageEnvelope, user: User) -> str | None:
    """The address a follow-up about this commitment will go to: the one on the message itself, not one read out of its text.

    A message someone sent to the person: whoever sent it. A message the person sent: the one recipient it went to (if there
    were several, the one the model named - but only if it really is among them). Otherwise nobody, and the person is asked."""
    own = user.email.lower()
    if message.direction == "INBOUND":
        sender = (message.sender_email or "").lower()
        return sender if sender and sender != own else None
    others = [r.lower() for r in message.recipients if r.lower() != own]
    named = (hinted or "").lower()
    if named in others:
        return named
    return others[0] if len(others) == 1 else None


def _origin_name(hinted_name: str | None, hinted_email: str | None, origin: str | None, message: MessageEnvelope) -> str | None:
    """A name is only attached to an address when they are known to belong together."""
    if origin is None:
        return hinted_name  # nobody to write to, but "waiting on Marcus" is still worth showing
    if message.direction == "INBOUND" and origin == (message.sender_email or "").lower():
        return message.sender_name or (hinted_name if (hinted_email or "").lower() == origin else None)
    return hinted_name if (hinted_email or "").lower() == origin else None


def _obligation_fields(analysis: Analysis, message: MessageEnvelope, user: User, settings: Settings, status: S) -> dict[str, Any]:
    ext, res = analysis.extraction, analysis.resolution
    tz = get_zone(user.timezone, settings.default_timezone)
    counterparty_email = _origin_address(ext.counterparty_email, message, user)
    counterparty_name = _origin_name(ext.counterparty_name, ext.counterparty_email, counterparty_email, message)
    owner = "me" if ext.owner == "SELF" else (ext.counterparty_name or ext.counterparty_email or "someone else")
    reasons = analysis.decision.reasons if analysis.decision.action == "REVIEW" else []
    return {
        "title": ext.title or "Untitled commitment",
        "action": ext.action,
        "source": message.source_type,
        "source_reference": message.external_id,
        "obligation_type": ext.obligation_type,
        "status": status,
        "priority": ext.priority,
        "confidence": ext.confidence,
        "due_at": res.due_at,
        "due_precision": res.precision,
        "due_text": ext.deadline_text,
        "due_timezone": res.timezone,
        "due_resolution": res.as_dict(analysis.reference) if (res.due_at or res.ambiguity) else None,
        "ambiguity": _join(*reasons, res.ambiguity, ext.ambiguity),
        "owner": owner[:200],
        "counterparty_name": (counterparty_name or None) and counterparty_name[:200],
        "counterparty_email": counterparty_email,
        "requires_confirmation": ext.requires_confirmation,
        "recurrence": ext.recurrence,
        "entities": [e.model_dump() for e in ext.entities],
        "fingerprint": dedup.fingerprint(ext.title or "", local_date(res.due_at, tz) if res.due_at else None),
    }


def _model_info(ev: MessageExtracted) -> dict[str, Any]:
    a = ev.analysis or {}
    return {"model": a.get("model"), "provider": a.get("provider")}


def should_notify_externally(ob: Obligation, now: datetime) -> bool:
    """'Notify if necessary': everything gets an in-app entry; email/Telegram only when it matters."""
    return (
        ob.status == S.NEEDS_REVIEW
        or ob.priority in (Priority.HIGH, Priority.URGENT)
        or ob.requires_confirmation
        or (ob.due_at is not None and ob.due_at <= now + EXTERNAL_NOTIFY_WITHIN)
    )


# ------------------------------------------------------------------------------------ the entry point
def handle_extracted_message(db: Session, ev: MessageExtracted, now: datetime, settings: Settings, run_id: uuid.UUID | None = None) -> dict[str, Any]:
    user = db.scalar(select(User).where(User.email == ev.user_email.strip().lower()))
    if user is None:
        raise NotFoundError("No CommitmentOS account for this mailbox owner")
    try:
        message = MessageEnvelope.model_validate(ev.message)
    except ValueError as exc:
        raise ValidationFailed(f"Invalid message envelope: {exc}") from exc
    actor_n8n = Actor.n8n(ev.n8n_execution_id)

    existing = _find_source(db, user.id, message, lock=True)
    if existing is not None and existing.disposition != SourceDisposition.EXTRACTION_FAILED:
        return {
            "disposition": existing.disposition.value,
            "duplicate_event": True,
            "obligation_id": str(existing.obligation_id) if existing.obligation_id else None,
        }

    body, _ = sanitize_text(message.body, settings.llm_max_input_chars)
    subject, _ = sanitize_text(message.subject or "", 500)
    content_hash = dedup.content_hash(f"{subject}\n{body}")
    common = dict(
        user_id=user.id, source_type=message.source_type, external_id=message.external_id, thread_id=message.thread_id,
        rfc_message_id=message.rfc_message_id, received_at=message.received_at or now, content_hash=content_hash,
    )

    # ---- extraction failed upstream (LLM down / refused / invalid output): record it visibly ----
    if ev.extraction_status != "OK" or ev.extraction is None:
        return _record_failure(db, user, message, existing, common, ev.extraction_status, ev.extraction_error, now, settings, run_id, actor_n8n)

    try:
        validated = validate_extraction(ev.extraction, f"{subject}\n{body}", settings, sender_email=message.sender_email, known_addresses=message.recipients)
    except InvalidOutput as exc:
        return _record_failure(db, user, message, existing, common, "INVALID_OUTPUT", "; ".join(exc.reasons)[:400], now, settings, run_id, actor_n8n)
    if ev.analysis:
        validated.confidence_notes = [*[str(n) for n in ev.analysis.get("confidence_notes", [])][:10], *validated.confidence_notes]
        validated.warnings = [*[str(w) for w in ev.analysis.get("warnings", [])][:10], *validated.warnings]
        raw = ev.analysis.get("confidence_raw")
        if isinstance(raw, int | float):
            validated.confidence_raw = float(raw)
    analysis = analyse(validated, message, user, settings, now)
    decision = analysis.decision
    ext = analysis.extraction

    source = existing or Source(**common, disposition=SourceDisposition.NOT_OBLIGATION, created_at=now, updated_at=now)
    if existing is not None:
        for key, value in common.items():
            setattr(existing, key, value)
    else:
        try:
            with db.begin_nested():
                db.add(source)
                db.flush()
        except IntegrityError:  # a concurrent duplicate webhook won the race
            winner = _find_source(db, user.id, message)
            return {"disposition": winner.disposition.value if winner else "DUPLICATE", "duplicate_event": True,
                    "obligation_id": str(winner.obligation_id) if winner and winner.obligation_id else None}

    audit.record(db, AuditEventType.MESSAGE_RECEIVED, "Message received and analysed", user_id=user.id, actor=actor_n8n, source_id=source.id, run_id=run_id,
                 data={"source": message.source_type}, now=now)
    audit.record(
        db, AuditEventType.AI_CLASSIFIED,
        f"Classified as {ext.obligation_type.value.replace('_', ' ').lower()} with {ext.confidence:.0%} confidence" if ext.is_obligation
        else f"Not a commitment ({ext.confidence:.0%} sure)",
        user_id=user.id, actor=Actor.ai(_model_info(ev)["model"]), source_id=source.id, run_id=run_id,
        data={**_model_info(ev), "is_obligation": ext.is_obligation, "confidence": ext.confidence, "confidence_raw": validated.confidence_raw,
              "confidence_notes": validated.confidence_notes, "warnings": validated.warnings, "decision": decision.as_dict()},
        now=now,
    )
    snapshot = {**analysis.snapshot(), **_model_info(ev)}

    # ---- not an obligation: keep no content ----
    if decision.action == "IGNORE":
        source.disposition = SourceDisposition.NOT_OBLIGATION
        source.subject = source.excerpt = source.sender_email = source.sender_name = None
        source.extraction = None
        audit.record(db, AuditEventType.NOT_AN_OBLIGATION, "No commitment found in this message", user_id=user.id, actor=Actor.system("ingestion"),
                     source_id=source.id, run_id=run_id, data={"confidence": ext.confidence}, now=now)
        db.flush()
        return {"disposition": "NOT_OBLIGATION", "source_id": str(source.id), "decision": decision.as_dict()}

    # from here on the message is evidence for something: keep just enough to verify it
    source.subject = subject or None
    source.sender_email, source.sender_name = message.sender_email, message.sender_name
    source.excerpt = _excerpt(message, analysis, body)
    source.extraction = snapshot

    duplicate = find_duplicate(db, user, message, analysis, settings, now)
    if duplicate is not None:
        return _merge_duplicate(db, user, source, duplicate, analysis, now, run_id, settings)

    if decision.action == "CANDIDATE":
        source.disposition = SourceDisposition.CANDIDATE
        fields = _obligation_fields(analysis, message, user, settings, S.NEEDS_REVIEW)
        candidate = DetectionCandidate(
            user_id=user.id, source_id=source.id, title=fields["title"], action=fields["action"], obligation_type=fields["obligation_type"],
            confidence=ext.confidence, reason="LOW_CONFIDENCE",
            extraction={**snapshot, "obligation_fields": _jsonable_fields(fields)}, created_at=now, updated_at=now,
        )
        db.add(candidate)
        db.flush()
        audit.record(db, AuditEventType.CANDIDATE_STORED, "Low-confidence detection stored as a candidate (no commitment created)", user_id=user.id,
                     actor=Actor.system("ingestion"), source_id=source.id, run_id=run_id, data={"candidate_id": candidate.id, "confidence": ext.confidence, "reasons": decision.reasons}, now=now)
        return {"disposition": "CANDIDATE", "candidate_id": str(candidate.id), "source_id": str(source.id), "decision": decision.as_dict()}

    status = decision.status or S.NEEDS_REVIEW
    fields = _obligation_fields(analysis, message, user, settings, status)
    ob = Obligation(user_id=user.id, acknowledged_at=None, created_at=now, updated_at=now, **fields)
    db.add(ob)
    db.flush()
    source.obligation_id = ob.id
    source.disposition = SourceDisposition.OBLIGATION_CREATED if status == S.OPEN else SourceDisposition.NEEDS_REVIEW
    source.role = "PRIMARY"

    audit.record(db, AuditEventType.COMMITMENT_DETECTED, "Commitment detected in the message", user_id=user.id, obligation_id=ob.id, actor=Actor.ai(_model_info(ev)["model"]),
                 source_id=source.id, run_id=run_id,
                 data={"due_at": ob.due_at, "precision": ob.due_precision, "ambiguous": analysis.resolution.ambiguous, "deadline_method": analysis.resolution.method}, now=now)
    audit.record(db, AuditEventType.OBLIGATION_CREATED, "Commitment created and now tracked" if status == S.OPEN else "Commitment created, waiting for your review", user_id=user.id, obligation_id=ob.id,
                 actor=Actor.system("ingestion"), source_id=source.id, run_id=run_id,
                 data={"decision": decision.action, "reasons": decision.reasons, "confidence": ob.confidence,
                       "thresholds": {"high": settings.confidence_high, "medium": settings.confidence_medium}}, now=now)
    if status == S.OPEN and ob.requires_confirmation:
        lifecycle.transition(db, ob, S.ACTION_REQUIRED, actor=Actor.system("ingestion"), now=now, reason="a reply/confirmation is expected")
    monitor.refresh_schedule(ob, user, now, settings)

    # ---- notify (in-app always; email/Telegram only if necessary) ----
    policy = policy_for(user, settings)
    kind = NotificationKind.DETECTED if status == S.OPEN else NotificationKind.NEEDS_REVIEW
    content = messages.build(kind, ob, policy.tz, now, settings)
    created = notifications.queue(db, user=user, ob=ob, kind=kind, rung_key="CREATED", content=content, policy=policy, now=now,
                                  external=should_notify_externally(ob, now), run_id=run_id)
    if created:
        audit.record(db, AuditEventType.NOTIFICATION_QUEUED, f"{'You were notified' if status == S.OPEN else 'Review requested'} ({messages.channels_phrase([n.channel.value for n in created])})",
                     user_id=user.id, obligation_id=ob.id, actor=Actor.system("notifier"), run_id=run_id,
                     data={"channels": [n.channel for n in created], "kind": kind, "external": should_notify_externally(ob, now)}, now=now)
    return {
        "disposition": source.disposition.value, "obligation_id": str(ob.id), "status": ob.status.value, "source_id": str(source.id),
        "decision": decision.as_dict(), "notifications": len(created),
        "due_at": ob.due_at.isoformat() if ob.due_at else None, "confidence": ob.confidence,
    }


def _jsonable_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return audit.jsonable(fields)


def _merge_duplicate(
    db: Session, user: User, source: Source, dup: DuplicateMatch, analysis: Analysis, now: datetime,
    run_id: uuid.UUID | None, settings: Settings,
) -> dict[str, Any]:
    ob = dup.obligation
    source.obligation_id = ob.id
    source.disposition = SourceDisposition.DUPLICATE
    source.role = "DUPLICATE"
    closed = ob.status in TERMINAL_STATUSES
    audit.record(
        db, AuditEventType.DUPLICATE_MERGED,
        "Matched an existing commitment; attached as another source" + (f" (which is already {ob.status.value.lower()})" if closed else ""),
        user_id=user.id, obligation_id=ob.id, actor=Actor.system("dedup"), source_id=source.id, run_id=run_id,
        data={"score": dup.score, "reasons": list(dup.reasons), "matched_status": ob.status,
              "deadline_conflict": dup.deadline_conflict, "new_due_at": analysis.resolution.due_at, "existing_due_at": ob.due_at},
        now=now,
    )
    notified = 0
    if dup.deadline_conflict and not closed:
        content = messages.Content(
            f"The deadline may have changed: {ob.title}",
            ["A newer message in the same conversation mentions a different date for this commitment. "
             "Nothing was changed automatically - open it and edit the deadline if the new date is right."],
            [{"label": "Open in CommitmentOS", "url": f"{settings.public_web_url.rstrip('/')}/obligations/{ob.id}"}],
        )
        made = notifications.queue(db, user=user, ob=ob, kind=NotificationKind.NEEDS_REVIEW, rung_key=f"DEADLINE_CONFLICT:{source.id}",
                                   content=content, policy=policy_for(user, settings), now=now, external=False, run_id=run_id)
        notified = len(made)
    db.flush()
    return {"disposition": "DUPLICATE", "obligation_id": str(ob.id), "source_id": str(source.id), "score": dup.score,
            "deadline_conflict": dup.deadline_conflict, "notifications": notified}


def _record_failure(db: Session, user: User, message: MessageEnvelope, existing: Source | None, common: dict[str, Any], status: str,
                    error: str | None, now: datetime, settings: Settings, run_id: uuid.UUID | None, actor: Actor) -> dict[str, Any]:
    """The message could not be analysed. Record it (ids + sender/subject to identify it, never the body) and tell the user."""
    source = existing
    if source is None:
        source = Source(**common, disposition=SourceDisposition.EXTRACTION_FAILED, created_at=now, updated_at=now)
        try:
            with db.begin_nested():
                db.add(source)
                db.flush()
        except IntegrityError:
            source = _find_source(db, user.id, message)
    assert source is not None
    source.disposition = SourceDisposition.EXTRACTION_FAILED
    source.sender_email, source.sender_name = message.sender_email, message.sender_name
    source.subject = (message.subject or None) and message.subject[:500]
    source.extraction = {"error_status": status, "error": (error or "")[:400]}
    audit.record(db, AuditEventType.EXTRACTION_FAILED, f"Could not analyse the message ({status.replace('_', ' ').lower()})", user_id=user.id, actor=actor,
                 source_id=source.id, run_id=run_id, data={"status": status, "error": (error or "")[:300]}, now=now)
    policy = policy_for(user, settings)
    content = messages.Content(
        "CommitmentOS couldn't analyse a message",
        [f"A message from {message.sender_email or 'an unknown sender'} could not be analysed ({status.replace('_', ' ').lower()}). "
         "No commitment was created from it. The failure is listed under Automations; you can retry the workflow run from n8n."],
        [{"label": "See automation activity", "url": f"{settings.public_web_url.rstrip('/')}/automations"}],
    )
    notifications.queue(db, user=user, ob=None, kind=NotificationKind.ACTION_RESULT, rung_key=f"EXTRACT_FAIL:{message.external_id}", content=content,
                        policy=policy, now=now, external=False, run_id=run_id)
    db.flush()
    return {"disposition": "EXTRACTION_FAILED", "source_id": str(source.id), "status": status, "error": (error or "")[:300]}


# ------------------------------------------------------------------------------------ candidates
def promote_candidate(db: Session, user: User, candidate: DetectionCandidate, now: datetime, settings: Settings) -> Obligation:
    if candidate.status != CandidateStatus.PENDING:
        raise ConflictError(f"This candidate is already {candidate.status.value.lower()}", code="CANDIDATE_NOT_PENDING")
    fields = dict(candidate.extraction.get("obligation_fields", {}))
    fields["status"] = S.NEEDS_REVIEW
    fields["source"] = SourceType(fields["source"])
    fields["obligation_type"] = ObligationType(fields["obligation_type"])
    fields["priority"] = Priority(fields["priority"])
    fields["due_precision"] = DuePrecision(fields["due_precision"]) if fields.get("due_precision") else None
    fields["recurrence"] = Recurrence(fields["recurrence"]) if fields.get("recurrence") else None
    fields["due_at"] = datetime.fromisoformat(fields["due_at"]).astimezone(UTC) if fields.get("due_at") else None
    ob = Obligation(user_id=user.id, acknowledged_at=now, created_at=now, updated_at=now, **fields)
    db.add(ob)
    db.flush()
    src = db.get(Source, candidate.source_id)
    if src is not None:
        src.obligation_id = ob.id
        src.role = "PRIMARY"
    candidate.status = CandidateStatus.PROMOTED
    candidate.promoted_obligation_id = ob.id
    audit.record(db, AuditEventType.CANDIDATE_PROMOTED, "Candidate promoted to a commitment for review", user_id=user.id, obligation_id=ob.id,
                 actor=Actor.user(user), data={"candidate_id": candidate.id}, now=now)
    monitor.refresh_schedule(ob, user, now, settings)
    return ob


def discard_candidate(db: Session, user: User, candidate: DetectionCandidate, now: datetime) -> None:
    if candidate.status == CandidateStatus.DISCARDED:
        return
    if candidate.status != CandidateStatus.PENDING:
        raise ConflictError(f"This candidate is already {candidate.status.value.lower()}", code="CANDIDATE_NOT_PENDING")
    candidate.status = CandidateStatus.DISCARDED
    audit.record(db, AuditEventType.CANDIDATE_DISCARDED, "Candidate discarded", user_id=user.id, actor=Actor.user(user), data={"candidate_id": candidate.id}, now=now)

