"""Extraction orchestration.

    sanitise -> prompt -> LLM call (bounded retries) -> validate (+ bounded "repair" retry)
             -> deterministic deadline resolution -> confidence-threshold decision

The LLM contributes classification and extracted *text*. Everything that decides what happens - dates,
thresholds, whether something is created - is code in this module and ``services.deadlines``.
This module is pure (no database): persistence lives in ``services.ingestion``.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from enum import StrEnum
from typing import Any, Literal

from app.config import Settings
from app.enums import ObligationStatus, ObligationType
from app.models import User
from app.services.deadlines import Resolution, ResolverContext, resolve_deadline
from app.services.extraction.llm import LLMClient, LLMError, LLMResponse
from app.services.extraction.prompt import SYSTEM_PROMPT, build_user_prompt, repair_prompt
from app.services.extraction.schema import LLM_JSON_SCHEMA, LLMExtraction, MessageEnvelope
from app.services.extraction.validate import (
    PENALTY_DATE_DISAGREEMENT,
    InvalidOutput,
    ValidatedExtraction,
    validate_extraction,
)
from app.services.reminders import preferences_of
from app.services.timeutil import get_zone, local_to_utc, to_local

log = logging.getLogger("commitmentos.extraction")

# Obligation types where "when" is intrinsic: a missing deadline means a human should look.
DEADLINE_TYPES = frozenset(
    {
        ObligationType.DEADLINE,
        ObligationType.PAYMENT,
        ObligationType.APPOINTMENT,
        ObligationType.INTERVIEW,
        ObligationType.DOCUMENT_REQUEST,
        ObligationType.RETURN,
        ObligationType.RENEWAL,
    }
)


class ExtractionStatus(StrEnum):
    OK = "OK"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_QUOTA_EXHAUSTED = "LLM_QUOTA_EXHAUSTED"
    LLM_REFUSED = "LLM_REFUSED"
    LLM_REJECTED = "LLM_REJECTED"
    INVALID_OUTPUT = "INVALID_OUTPUT"


_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")  # raw string: the regex engine decodes the escapes
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(text: str, max_chars: int) -> tuple[str, bool]:
    """Safe, bounded text for the LLM and for storage. Returns (clean, was_truncated)."""
    t = unicodedata.normalize("NFC", text or "")
    t = _ZERO_WIDTH.sub("", _CONTROL.sub("", t.replace("\r\n", "\n").replace("\r", "\n")))
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if len(t) <= max_chars:
        return t, False
    cut = t[:max_chars]
    boundary = max(cut.rfind("\n"), cut.rfind(". "), cut.rfind(" "))
    return (cut[:boundary] if boundary > max_chars * 0.8 else cut).rstrip(), True


@dataclass
class Decision:
    action: Literal["CREATE", "REVIEW", "CANDIDATE", "IGNORE"]
    status: ObligationStatus | None
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"action": self.action, "status": self.status.value if self.status else None, "reasons": self.reasons}


@dataclass
class Analysis:
    """Everything derived deterministically from a validated extraction."""

    validated: ValidatedExtraction
    resolution: Resolution
    decision: Decision
    reference: datetime

    @property
    def extraction(self) -> LLMExtraction:
        return self.validated.data

    def snapshot(self) -> dict[str, Any]:
        """Persisted with the source so the detail page can explain the detection later."""
        e = self.extraction
        return {
            "explanation": e.explanation,
            "source_context": e.source_context,
            "deadline_text": e.deadline_text,
            "confidence_raw": self.validated.confidence_raw,
            "confidence": e.confidence,
            "confidence_notes": self.validated.confidence_notes,
            "warnings": self.validated.warnings,
            "decision": self.decision.as_dict(),
            "resolution": self.resolution.as_dict(self.reference),
            "obligation_type": e.obligation_type.value,
            "priority": e.priority.value,
            "owner": e.owner,
            "requires_confirmation": e.requires_confirmation,
            "entities": [x.model_dump() for x in e.entities],
        }


@dataclass
class ExtractionOutcome:
    status: ExtractionStatus
    retryable: bool = False
    error: str | None = None
    analysis: Analysis | None = None
    provider: str | None = None
    model: str | None = None
    latency_ms: int = 0
    attempts: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    input_truncated: bool = False

    def as_response(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "status": self.status.value,
            "retryable": self.retryable,
            "error_message": self.error,  # NOT "error": n8n treats any item with a json.error as a failed one and retries the node
            "provider": self.provider,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
            "tokens": {"input": self.input_tokens, "output": self.output_tokens},
            "input_truncated": self.input_truncated,
            "decision": None,
            "extraction": None,
            "analysis": None,
        }
        if self.analysis is not None:
            a = self.analysis
            base["decision"] = a.decision.as_dict()
            base["extraction"] = a.extraction.model_dump(mode="json")
            base["analysis"] = {
                "confidence_raw": a.validated.confidence_raw,
                "confidence_notes": a.validated.confidence_notes,
                "warnings": a.validated.warnings,
                "resolution": a.resolution.as_dict(a.reference),
                "model": self.model,
                "provider": self.provider,
            }
        return base


# ------------------------------------------------------------------------------------ deterministic part
def build_resolver_context(user: User, settings: Settings, reference: datetime, now: datetime) -> ResolverContext:
    prefs = preferences_of(user)
    return ResolverContext(
        reference=reference,
        tz=get_zone(user.timezone, settings.default_timezone),
        business_day_end=time.fromisoformat(prefs.business_day_end) if prefs.business_day_end else settings.business_day_end_time,
        date_order=prefs.date_order or settings.default_date_order,
        now=now,
    )


def _parse_iso(value: str | None, ctx: ResolverContext) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = local_to_utc(parsed, ctx.tz)
    return parsed.astimezone(UTC)


def reconcile_deadline(v: ValidatedExtraction, ctx: ResolverContext) -> Resolution:
    """Resolve the deadline from the *wording*; the AI's own ISO date is only ever a cross-check."""
    ext = v.data
    res = resolve_deadline(ext.deadline_text, ctx) if ext.deadline_text else None
    if (res is None or res.due_at is None) and ext.source_context:
        from_quote = resolve_deadline(ext.source_context, ctx, explicit_only=True)
        if from_quote.due_at is not None:
            res = from_quote
            res.warnings.append("the deadline was read from the quoted sentence")
    if res is None:
        res = resolve_deadline(None, ctx)

    suggested = _parse_iso(ext.due_at, ctx)
    if suggested is not None:
        if res.due_at is None:
            res.llm_suggestion = suggested.isoformat()
            res.warnings.append("the AI suggested a date but no matching wording was found in the message, so it was not used")
        elif to_local(suggested, ctx.tz).date() != to_local(res.due_at, ctx.tz).date():
            ai_day = to_local(suggested, ctx.tz).date()
            note = f"the AI read the date as {ai_day:%a %b} {ai_day.day} but the wording resolves to a different day"
            res.ambiguous = True
            res.ambiguity = f"{res.ambiguity}; {note}" if res.ambiguity else note
            res.llm_suggestion = suggested.isoformat()
            v.penalise(PENALTY_DATE_DISAGREEMENT, "the AI's date and the rule-based reading disagree")
    ext.due_at = None  # consumed: keeps re-analysis (at commit time) idempotent
    return res


def decide(v: ValidatedExtraction, res: Resolution, settings: Settings) -> Decision:
    ext = v.data
    if not ext.is_obligation:
        return Decision("IGNORE", None, ["the message does not contain an obligation"])
    confidence = ext.confidence
    reasons: list[str] = []
    if res.ambiguous:
        reasons.append("the deadline is ambiguous")
    if res.due_at is None and ext.obligation_type in DEADLINE_TYPES:
        reasons.append("no concrete deadline was found")
    if res.in_past:
        reasons.append("the deadline has already passed")
    if not v.context_grounded:
        reasons.append("no supporting quote could be verified in the message")
    if confidence >= settings.confidence_high and not reasons:
        return Decision("CREATE", ObligationStatus.OPEN, [f"confidence {confidence:.0%} meets the auto-create threshold"])
    if confidence >= settings.confidence_medium:
        if not reasons:
            reasons.append(f"confidence {confidence:.0%} is below the auto-create threshold ({settings.confidence_high:.0%})")
        return Decision("REVIEW", ObligationStatus.NEEDS_REVIEW, reasons)
    return Decision("CANDIDATE", None, [f"confidence {confidence:.0%} is below the review threshold ({settings.confidence_medium:.0%})", *reasons])


def analyse(v: ValidatedExtraction, message: MessageEnvelope, user: User, settings: Settings, now: datetime) -> Analysis:
    reference = message.received_at or now
    ctx = build_resolver_context(user, settings, reference, now)
    resolution = reconcile_deadline(v, ctx)
    return Analysis(validated=v, resolution=resolution, decision=decide(v, resolution, settings), reference=reference)


# ------------------------------------------------------------------------------------ the LLM call
STATUS_BY_CODE = {
    "NOT_CONFIGURED": ExtractionStatus.NOT_CONFIGURED,
    "LLM_REJECTED": ExtractionStatus.LLM_REJECTED,
    "LLM_REFUSED": ExtractionStatus.LLM_REFUSED,
    "LLM_TIMEOUT": ExtractionStatus.LLM_TIMEOUT,
    "LLM_QUOTA_EXHAUSTED": ExtractionStatus.LLM_QUOTA_EXHAUSTED,
    "LLM_UNAVAILABLE": ExtractionStatus.LLM_UNAVAILABLE,
}


def extract_message(message: MessageEnvelope, user: User, settings: Settings, llm: LLMClient, now: datetime) -> ExtractionOutcome:
    tz = get_zone(user.timezone, settings.default_timezone)
    reference = message.received_at or now
    body, truncated = sanitize_text(message.body, settings.llm_max_input_chars)
    subject, _ = sanitize_text(message.subject or "", 500)
    if not body and not subject:
        return ExtractionOutcome(ExtractionStatus.INVALID_OUTPUT, error="the message has no text to analyse", input_truncated=False)

    grounding_text = f"{subject}\n{body}"
    conversation = [{"role": "user", "content": build_user_prompt(message, body, reference, tz)}]
    outcome = ExtractionOutcome(ExtractionStatus.INVALID_OUTPUT, provider=llm.provider, model=llm.model, input_truncated=truncated)
    reasons: list[str] = []
    validated: ValidatedExtraction | None = None
    response: LLMResponse | None = None

    for _ in range(1 + settings.llm_max_repair_attempts):
        outcome.attempts += 1
        try:
            response = llm.complete_json(
                system=SYSTEM_PROMPT, messages=conversation, schema=LLM_JSON_SCHEMA, max_output_tokens=settings.llm_max_output_tokens
            )
        except LLMError as exc:
            outcome.status = STATUS_BY_CODE.get(exc.code, ExtractionStatus.LLM_UNAVAILABLE)
            outcome.retryable = exc.retryable
            outcome.error = str(exc)
            log.warning("extraction failed: provider=%s status=%s", llm.provider, outcome.status.value)
            return outcome
        outcome.model = response.model
        outcome.latency_ms += response.latency_ms
        outcome.input_tokens = (outcome.input_tokens or 0) + (response.input_tokens or 0)
        outcome.output_tokens = (outcome.output_tokens or 0) + (response.output_tokens or 0)
        try:
            if response.stop_reason == "max_tokens":
                raise InvalidOutput(["the reply hit the output limit and was cut off"])
            validated = validate_extraction(response.text, grounding_text, settings, sender_email=message.sender_email)
            break
        except InvalidOutput as exc:
            reasons = exc.reasons
            log.warning("invalid LLM output (attempt %d): %s", outcome.attempts, "; ".join(reasons)[:300])
            conversation = [
                *conversation,
                {"role": "assistant", "content": response.text[:4000]},
                {"role": "user", "content": repair_prompt(reasons)},
            ]

    if validated is None:
        outcome.status = ExtractionStatus.INVALID_OUTPUT
        outcome.error = "The model's reply could not be validated: " + "; ".join(reasons)[:400]
        return outcome

    if truncated:
        validated.warnings.append(f"the message was longer than {settings.llm_max_input_chars} characters; only the beginning was analysed")
    outcome.analysis = analyse(validated, message, user, settings, now)
    outcome.status = ExtractionStatus.OK
    log.info(
        "extraction ok: provider=%s model=%s decision=%s confidence=%.2f attempts=%d ms=%d request=%s",
        llm.provider, response.model if response else "-", outcome.analysis.decision.action,
        validated.data.confidence, outcome.attempts, outcome.latency_ms, response.request_id if response else "-",
    )
    return outcome
