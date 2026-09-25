from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel

from app.schemas.misc import (
    ApprovalOut,
    AutomationRunOut,
    CalendarEventOut,
    NotificationOut,
)
from app.schemas.obligation import ObligationOut, SourceOut, SuggestedAction


class Understanding(BaseModel):
    """'What CommitmentOS understood' - plain-language provenance, built only from stored facts."""

    origin: str
    explanation: str | None
    action: str | None
    source_context: str | None
    deadline_text: str | None
    deadline_explanation: str | None
    confidence: float
    confidence_notes: list[str]
    ambiguity: str | None
    alternatives: list[dt.datetime | str]
    detector: str | None


class ObligationDetail(BaseModel):
    obligation: ObligationOut
    understanding: Understanding
    sources: list[SourceOut]
    notifications: list[NotificationOut]
    approvals: list[ApprovalOut]
    calendar_events: list[CalendarEventOut]
    runs: list[AutomationRunOut]
    suggested_next_action: SuggestedAction
    follow_up_to: str | None = None
    follow_up_to_original: bool = False
    now: dt.datetime
    timezone: str
    extra: dict[str, Any] = {}
