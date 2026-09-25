"""The AI extraction contract.

``LLMExtraction`` is exactly what the model is asked to return - and nothing more. It is validated
strictly (unknown fields rejected, enums checked, ranges enforced) before any of it is trusted.
Everything the LLM says is a *proposal*: dates, thresholds, deduplication and state are decided by code.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.enums import ObligationType, Priority, Recurrence, SourceType


class LLMEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(max_length=40, description="e.g. organization, person, amount, document, location")
    value: str = Field(max_length=200)


class LLMExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    is_obligation: bool = Field(description="true only if the message contains something the recipient must do, attend, pay, renew, return, confirm, or remember")
    confidence: float = Field(ge=0.0, le=1.0, strict=True, description="0..1 certainty in this whole extraction (calibrated, see instructions)")
    title: str | None = Field(default=None, max_length=200, description="short imperative title, e.g. 'Submit internship documents'")
    action: str | None = Field(default=None, max_length=400, description="the concrete action expected, as a short phrase")
    obligation_type: ObligationType = Field(default=ObligationType.OTHER)
    priority: Priority = Field(default=Priority.MEDIUM, description="semantic urgency from the wording only, NOT from calculating dates")
    owner: Literal["SELF", "OTHER"] = Field(default="SELF", description="SELF = the recipient must act; OTHER = someone else promised to act")
    deadline_text: str | None = Field(default=None, max_length=200, description="the deadline phrase copied VERBATIM from the message (e.g. 'by Friday 5pm'); null if none")
    due_at: str | None = Field(default=None, max_length=64, description="ISO 8601, ONLY if the message states a complete calendar date; never compute it")
    source_context: str | None = Field(default=None, max_length=500, description="one short sentence copied VERBATIM from the message that justifies the obligation")
    explanation: str | None = Field(default=None, max_length=500, description="1-2 plain sentences: why this was (or was not) detected")
    ambiguity: str | None = Field(default=None, max_length=500, description="anything unclear about the deadline, action or who must act; null if clear")
    requires_confirmation: bool = Field(default=False, description="true if the sender expects the recipient to reply/confirm")
    counterparty_name: str | None = Field(default=None, max_length=200)
    counterparty_email: str | None = Field(default=None, max_length=320)
    recurrence: Recurrence | None = Field(default=None, description="only if the message says it repeats (monthly bill, weekly report, ...)")
    entities: list[LLMEntity] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def _title_required_for_obligations(self) -> LLMExtraction:
        if self.is_obligation and not (self.title and self.title.strip()):
            raise ValueError("title is required when is_obligation is true")
        return self


class MessageEnvelope(BaseModel):
    """One normalised incoming message (produced by n8n from Gmail or a webhook)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_type: SourceType = SourceType.GMAIL
    external_id: str = Field(min_length=1, max_length=512)
    thread_id: str | None = Field(default=None, max_length=512)
    rfc_message_id: str | None = Field(default=None, max_length=512)
    sender_email: str | None = Field(default=None, max_length=320)
    sender_name: str | None = Field(default=None, max_length=200)
    subject: str | None = Field(default=None, max_length=500)
    body: str = Field(default="", max_length=400_000)
    received_at: dt.datetime | None = None
    recipients: list[str] = Field(default_factory=list, max_length=20, description="To/Cc addresses; who a message the owner SENT was addressed to")
    direction: Literal["INBOUND", "OUTBOUND"] = "INBOUND"

    @model_validator(mode="after")
    def _aware(self) -> MessageEnvelope:
        if self.received_at is not None and self.received_at.tzinfo is None:
            raise ValueError("received_at must include a UTC offset")
        if self.sender_email:
            self.sender_email = self.sender_email.lower()
        self.recipients = sorted({r.strip().lower() for r in self.recipients if r and r.strip()})
        return self


def portable_json_schema(model: type[BaseModel] | None = None) -> dict[str, Any]:
    """JSON Schema for a contract (the extraction contract by default), reduced to the subset every provider accepts.

    Gemini's ``responseJsonSchema`` and most strict JSON-schema modes reject numeric/string
    length constraints, so those are stripped from what is *sent* - they are still enforced by the
    Pydantic model when the reply comes back. All properties are marked required (nullable ones
    are ``anyOf [T, null]``), which makes replies uniform and easy to validate.
    """
    schema = (model or LLMExtraction).model_json_schema()
    defs = schema.pop("$defs", {})

    strip = {"title", "default", "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "pattern", "examples"}

    def clean(node: Any) -> Any:
        if isinstance(node, list):
            return [clean(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return clean(defs[node["$ref"].split("/")[-1]])
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in strip:
                continue  # schema *annotations*
            if key == "properties":
                # keys of this mapping are field NAMES (one of them is literally "title"): never filter them
                out[key] = {name: clean(sub) for name, sub in value.items()}
            else:
                out[key] = clean(value)
        if out.get("type") == "object":
            out["required"] = list(out.get("properties", {}))
            out["additionalProperties"] = False
        return out

    return clean(schema)


LLM_JSON_SCHEMA: dict[str, Any] = portable_json_schema()
FIELD_NAMES = frozenset(LLMExtraction.model_fields)
