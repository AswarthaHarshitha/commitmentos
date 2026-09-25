"""Builders and a scriptable fake LLM for the extraction tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.services.extraction.llm import LLMResponse

QUOTE = "Please submit your signed internship documents by tomorrow 5pm"
BODY = f"Hi Alex,\n\nThanks for accepting the offer! {QUOTE} so we can finalise onboarding.\n\nBest,\nDana\nUniversity HR"
RECEIVED = "2026-09-23T15:00:00Z"  # Wed 11:00 in New York, the timezone the `alice` fixture user has


def make_extraction(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "is_obligation": True, "confidence": 0.95, "title": "Submit internship documents",
        "action": "Submit signed internship documents", "obligation_type": "DOCUMENT_REQUEST", "priority": "HIGH", "owner": "SELF",
        "deadline_text": "by tomorrow 5pm", "due_at": None, "source_context": QUOTE,
        "explanation": "The sender asks you to submit signed documents by 5pm tomorrow.", "ambiguity": None,
        "requires_confirmation": False, "counterparty_name": "Dana", "counterparty_email": "hr@example.org",
        "recurrence": None, "entities": [],
    }
    base.update(over)
    return base


def make_message(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "source_type": "GMAIL", "external_id": "gmail-001", "thread_id": "thread-1", "sender_email": "hr@example.org",
        "sender_name": "Dana Whitfield", "subject": "Internship paperwork", "body": BODY, "received_at": RECEIVED,
    }
    base.update(over)
    return base


class FakeLLM:
    """Scriptable stand-in for an LLM provider. Replies: dict -> JSON, str -> raw text, (text, stop_reason), or an Exception to raise.
    The last scripted item repeats forever."""

    provider = "fake"
    model = "fake-model-1"

    def __init__(self, *script: Any) -> None:
        self.script = list(script) or [make_extraction()]
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, *, system: str, messages: list[dict[str, str]], schema: dict[str, Any], max_output_tokens: int) -> LLMResponse:
        self.calls.append({"system": system, "messages": [dict(m) for m in messages], "schema": schema, "max_output_tokens": max_output_tokens})
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        stop = "end_turn"
        if isinstance(item, dict):
            text = json.dumps(item)
        elif isinstance(item, tuple):
            text, stop = item
        else:
            text = item
        return LLMResponse(text=text, provider=self.provider, model=self.model, stop_reason=stop, input_tokens=100, output_tokens=50, latency_ms=5)


def received(iso: str = RECEIVED) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(UTC)
