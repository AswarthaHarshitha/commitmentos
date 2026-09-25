"""Everything the commitment detail page shows beyond the row itself.

* ``suggest_next_action``  - deterministic rules, no LLM.
* ``build_timeline``       - what already happened (audit) + what the system WILL do (planned rungs).
* ``build_understanding``  - "what CommitmentOS understood": plain-language provenance so a person can
                             verify *why* an obligation exists (the trust feature).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import (
    ACTIVE_STATUSES,
    ApprovalAction,
    ApprovalStatus,
    NotificationKind,
    ObligationType,
    SourceType,
)
from app.enums import (
    ObligationStatus as S,
)
from app.models import ApprovalRequest, AuditEvent, CalendarEvent, Obligation, Source, User
from app.schemas.misc import TimelineEntry
from app.schemas.obligation import SuggestedAction
from app.services.messages import fmt_span, fmt_when
from app.services.reminders import build_ladder, policy_for
from app.services.timeutil import get_zone

_LABELS = {
    NotificationKind.REMINDER: "Reminder",
    NotificationKind.HIGH_PRIORITY_REMINDER: "High-priority reminder",
    NotificationKind.OVERDUE: "Overdue notice",
    NotificationKind.ESCALATION: "Escalation notice",
}


def suggest_next_action(
    ob: Obligation,
    now: datetime,
    *,
    has_calendar_event: bool,
    open_follow_up: bool,
    follow_up_to: str | None = None,
) -> SuggestedAction:
    base = f"/api/obligations/{ob.id}"
    if ob.status in (S.DETECTED, S.NEEDS_REVIEW):
        why = ob.ambiguity or f"Detected with {round(ob.confidence * 100)}% confidence - check it before it is tracked."
        return SuggestedAction(code="REVIEW", label="Review and accept", reason=why, endpoint=f"{base}/approve")
    if ob.status in (S.COMPLETED, S.DISMISSED):
        return SuggestedAction(code="NONE", label="Nothing to do", reason=f"This commitment is {ob.status.value.lower()}.")
    if ob.status in (S.OVERDUE, S.ESCALATED):
        return SuggestedAction(
            code="RESOLVE_OVERDUE",
            label="Complete it, or move the deadline",
            reason="The deadline has passed. If it is done, mark it complete; if the deadline changed, edit it.",
            endpoint=f"{base}/complete",
        )
    if ob.requires_confirmation and (follow_up_to or ob.counterparty_email) and not open_follow_up:
        return SuggestedAction(
            code="CONFIRM_WITH_SENDER",
            label="Reply to confirm",
            reason=(
                "A confirmation is expected from you. CommitmentOS can draft a reply to "
                f"{ob.counterparty_name or follow_up_to or ob.counterparty_email} for your approval."
            ),
            endpoint=f"{base}/follow-up",
        )
    if ob.obligation_type in (ObligationType.APPOINTMENT, ObligationType.INTERVIEW) and ob.due_at and not has_calendar_event:
        return SuggestedAction(
            code="ADD_TO_CALENDAR",
            label="Add to your calendar",
            reason="No calendar event is linked to this appointment.",
            endpoint=f"{base}/schedule",
        )
    if ob.due_at is None:
        return SuggestedAction(code="SET_DEADLINE", label="Set a deadline", reason="Without a deadline CommitmentOS cannot remind you.")
    remaining = ob.due_at - now
    if remaining <= timedelta(hours=24):
        return SuggestedAction(
            code="DO_IT_NOW",
            label="Finish this, then mark it complete",
            reason=f"Due in about {fmt_span(remaining)}.",
            endpoint=f"{base}/complete",
        )
    return SuggestedAction(
        code="WAIT",
        label="Nothing needed yet",
        reason=f"You will be reminded automatically as the deadline approaches (due in about {fmt_span(remaining)}).",
    )


def build_timeline(db: Session, ob: Obligation, user: User, now: datetime, settings: Settings) -> list[TimelineEntry]:
    events = db.scalars(
        select(AuditEvent).where(AuditEvent.obligation_id == ob.id).order_by(AuditEvent.created_at, AuditEvent.id)
    ).all()
    entries = [
        TimelineEntry(
            at=e.created_at,
            kind="past",
            event_type=e.event_type.value,
            message=e.message,
            actor=f"{e.actor_type.value.lower()}" + (f":{e.actor_id}" if e.actor_id and e.actor_type.value != "USER" else ""),
            data=e.data or {},
        )
        for e in events
    ]
    if ob.status in ACTIVE_STATUSES and ob.due_at is not None:
        policy = policy_for(user, settings)
        sent_keys = {e.data.get("rung") for e in events if e.event_type.value == "NOTIFICATION_QUEUED"}
        snoozed_until = ob.snoozed_until if ob.snoozed_until and ob.snoozed_until > now else None
        for rung in build_ladder(ob.due_at, ob.due_precision, policy):
            if rung.at <= now or rung.key in sent_keys:
                continue
            at = max(rung.at, snoozed_until) if snoozed_until else rung.at
            label = _LABELS.get(rung.kind, rung.kind.value)
            entries.append(
                TimelineEntry(
                    at=at,
                    kind="planned",
                    event_type=f"PLANNED_{rung.key}",
                    message=f"{label} scheduled",
                    actor="system",
                    data={"rung": rung.key, "when": fmt_when(at, None, policy.tz)},
                )
            )
    entries.sort(key=lambda e: (e.at, 0 if e.kind == "past" else 1))
    return entries


def build_understanding(ob: Obligation, primary: Source | None, tz_name: str) -> dict[str, Any]:
    """Plain-language provenance for the detail page. Built only from stored facts."""
    extraction = (primary.extraction if primary is not None else None) or {}
    resolution = ob.due_resolution or {}
    if ob.source == SourceType.MANUAL:
        return {
            "origin": "MANUAL",
            "explanation": "You created this commitment yourself.",
            "action": ob.action or ob.title,
            "source_context": None,
            "deadline_text": None,
            "deadline_explanation": _deadline_explanation(ob, resolution, tz_name),
            "confidence": ob.confidence,
            "confidence_notes": [],
            "ambiguity": ob.ambiguity,
            "alternatives": [],
            "detector": None,
        }
    return {
        "origin": ob.source.value,
        "explanation": extraction.get("explanation"),
        "action": ob.action or ob.title,
        "source_context": extraction.get("source_context"),
        "deadline_text": ob.due_text,
        "deadline_explanation": _deadline_explanation(ob, resolution, tz_name),
        "confidence": ob.confidence,
        "confidence_notes": extraction.get("confidence_notes", []),
        "ambiguity": ob.ambiguity,
        "alternatives": resolution.get("alternatives", []),
        "detector": extraction.get("model"),
    }


def _deadline_explanation(ob: Obligation, resolution: dict[str, Any], tz_name: str) -> str | None:
    method = resolution.get("method")
    if ob.due_at is None:
        return resolution.get("note") or ("No concrete deadline was found in the message." if ob.source != SourceType.MANUAL else None)
    if method == "MANUAL":
        return f"Set by you in {tz_name}."
    if method == "USER_EDIT":
        return "Changed by you."
    if method == "RECURRENCE":
        return "Calculated from the recurring schedule."
    text = ob.due_text
    zone = resolution.get("timezone", tz_name)
    parts: list[str] = []
    detail = resolution.get("explanation")  # already quotes the message's own words ('Read "..." as ...')
    if detail is None and text:
        parts.append(f"The message said \"{text}\".")
    ref = resolution.get("reference_time")
    if ref:
        try:
            received = fmt_when(datetime.fromisoformat(ref), None, get_zone(zone))
        except (ValueError, KeyError):
            received = None
        if received:
            parts.append(f"Relative words were read from when the message arrived ({received}).")
    if detail:
        parts.append(detail)
    parts.extend(resolution.get("warnings", []))
    return " ".join(p if p.endswith((".", "!", "?")) else f"{p}." for p in parts) or None


def open_follow_up(db: Session, ob: Obligation) -> bool:
    return (
        db.scalar(
            select(ApprovalRequest.id).where(
                ApprovalRequest.obligation_id == ob.id,
                ApprovalRequest.action_type == ApprovalAction.SEND_FOLLOW_UP,
                ApprovalRequest.status.in_([ApprovalStatus.PENDING, ApprovalStatus.APPROVED, ApprovalStatus.EXECUTING]),
            )
        )
        is not None
    )


def has_calendar_event(db: Session, ob: Obligation) -> bool:
    return db.scalar(select(CalendarEvent.id).where(CalendarEvent.obligation_id == ob.id)) is not None
