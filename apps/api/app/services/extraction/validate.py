"""Never trust raw LLM output.

    raw text -> JSON object -> normalise harmless drift -> STRICT pydantic validation
             -> grounding against the source message -> confidence adjustments

* Malformed / truncated / non-JSON replies raise ``InvalidOutput`` (with safe, content-free reasons).
* Hallucinated fields are dropped (and reported), never silently accepted.
* Hallucinated *evidence* is caught: the quoted sentence and the deadline phrase must literally occur in
  the message. If they do not they are discarded and the confidence is reduced - and a detection with no
  verifiable quote can never be auto-created (see ``CONFIDENCE_CAP``).
* Error descriptions never include the offending values (they may contain email content).
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from pydantic import ValidationError

from app.config import Settings
from app.services.extraction.schema import FIELD_NAMES, LLMExtraction

_BOM = re.compile(r"^\ufeff+")  # raw-string regex escape: immune to tools that rewrite literals
_NULLISH = {"", "null", "none", "n/a", "na", "nil", "unknown", "not specified", "not applicable", "-", "--"}
_BOOLS = {"true": True, "false": False, "yes": True, "no": False, "1": True, "0": False}
_TEXT_LIMITS = {
    "title": 200, "action": 400, "deadline_text": 200, "due_at": 64, "source_context": 500, "explanation": 500,
    "ambiguity": 500, "counterparty_name": 200, "counterparty_email": 320,
}
_ENUM_FIELDS = ("obligation_type", "priority", "owner", "recurrence")
_BOOL_FIELDS = ("is_obligation", "requires_confirmation")

# multiplicative penalties (each applied at most once)
PENALTY_UNGROUNDED_CONTEXT = 0.85
PENALTY_UNGROUNDED_DEADLINE = 0.80
PENALTY_OFF_CONTRACT = 0.95
PENALTY_DATE_DISAGREEMENT = 0.85


class InvalidOutput(Exception):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


@dataclass
class ValidatedExtraction:
    data: LLMExtraction
    warnings: list[str] = field(default_factory=list)
    unknown_fields: list[str] = field(default_factory=list)
    confidence_raw: float = 0.0
    confidence_notes: list[str] = field(default_factory=list)
    context_grounded: bool = False
    deadline_grounded: bool = False

    def penalise(self, factor: float, note: str) -> None:
        self.data.confidence = round(max(0.0, min(1.0, self.data.confidence * factor)), 4)
        self.confidence_notes.append(f"{note} (x{factor:g})")


# ------------------------------------------------------------------------------------ parsing
def parse_json_object(raw: str) -> dict[str, Any]:
    text = _BOM.sub("", (raw or "").strip())
    if not text:
        raise InvalidOutput(["the reply was empty"])
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1)
    for candidate in (text, _first_balanced_object(text)):
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
        raise InvalidOutput(["the reply is JSON but not an object"])
    raise InvalidOutput(["the reply is not valid JSON (it may have been truncated)"])


def _first_balanced_object(text: str) -> str | None:
    start = text.find("{")
    while start != -1:
        depth, in_str, escaped = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        start = text.find("{", start + 1)
    return None


# ------------------------------------------------------------------------------------ normalisation
def _nullish(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in _NULLISH)


def normalise(data: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    """Fix harmless formatting drift; report (never hide) everything that was changed or dropped."""
    warnings: list[str] = []
    out: dict[str, Any] = {}
    unknown: list[str] = []
    for raw_key, value in data.items():
        key = str(raw_key).strip().lower()
        if key not in FIELD_NAMES:
            unknown.append(key[:40])
            continue
        out[key] = value
    if unknown:
        warnings.append(f"ignored {len(unknown)} unexpected field(s): {', '.join(sorted(set(unknown)))}")

    for key, limit in _TEXT_LIMITS.items():
        if key not in out:
            continue
        value = out[key]
        if _nullish(value):
            out[key] = None
        elif isinstance(value, str):
            value = value.strip()
            if len(value) > limit:
                value = value[: limit - 1].rstrip() + "…"
                warnings.append(f"{key} was longer than {limit} characters and was shortened")
            out[key] = value
        else:
            out[key] = str(value)[:limit]
    for key in _ENUM_FIELDS:
        if key in out:
            value = out[key]
            out[key] = None if _nullish(value) else re.sub(r"[\s\-]+", "_", str(value).strip().upper())
    for key in _BOOL_FIELDS:
        if isinstance(out.get(key), str) and out[key].strip().lower() in _BOOLS:
            out[key] = _BOOLS[out[key].strip().lower()]
    if "confidence" in out:
        conf = out["confidence"]
        if isinstance(conf, str):
            text = conf.strip()
            percent = text.endswith("%")
            try:
                conf = float(text.rstrip("%").strip()) / (100 if percent else 1)
            except ValueError:
                conf = out["confidence"]
        if isinstance(conf, int | float) and not isinstance(conf, bool) and 2 <= conf <= 100:  # 1<x<2 is just out of range, not a percentage
            conf = conf / 100
            warnings.append("confidence was given as a percentage and was converted")
        out["confidence"] = conf
    entities = out.get("entities")
    if entities is None or _nullish(entities):
        out["entities"] = []
    elif isinstance(entities, list):
        kept = []
        for item in entities:
            if isinstance(item, dict) and isinstance(item.get("type"), str) and isinstance(item.get("value"), str):
                kept.append({"type": item["type"].strip()[:40], "value": item["value"].strip()[:200]})
        if len(kept) != len(entities):
            warnings.append("some malformed entities were dropped")
        out["entities"] = kept[:12]
    else:
        out["entities"] = []
        warnings.append("entities was not a list and was ignored")
    return out, warnings, sorted(set(unknown))


def safe_errors(exc: ValidationError) -> list[str]:
    """Field path + error type + our own message. Never the input value."""
    return [f"{'.'.join(str(p) for p in e['loc']) or 'reply'}: {e['type']} ({e['msg']})"[:200] for e in exc.errors()][:8]


# ------------------------------------------------------------------------------------ grounding
def squash(text: str) -> str:
    return re.sub(r"\W+", " ", unicodedata.normalize("NFKC", text).lower()).strip()


def is_grounded(fragment: str | None, haystack: str, threshold: float) -> bool:
    """Does ``fragment`` really occur in ``haystack`` (whitespace/punctuation/case-insensitive)?

    An exact hit passes; otherwise the longest common block must cover ``threshold`` of the fragment,
    which tolerates a dropped comma or a trailing word without accepting an invented sentence."""
    frag = squash(fragment or "")
    if not frag:
        return False
    if frag in haystack:
        return True
    match = SequenceMatcher(None, frag, haystack, autojunk=False).find_longest_match(0, len(frag), 0, len(haystack))
    return match.size / len(frag) >= threshold


def validate_extraction(
    source: str | dict[str, Any], message_text: str, settings: Settings, *, sender_email: str | None = None, known_addresses: Iterable[str] = ()
) -> ValidatedExtraction:
    """Validate an LLM reply (raw text) or a previously validated dict against the message it came from.

    `known_addresses` are addresses from the message's own headers (its recipients): as trustworthy as the sender, and not part of
    the text a model reads, so an address the model names is accepted if it is one of them."""
    raw = parse_json_object(source) if isinstance(source, str) else dict(source)
    normalised, warnings, unknown = normalise(raw)
    try:
        data = LLMExtraction.model_validate(normalised)
    except ValidationError as exc:
        raise InvalidOutput(safe_errors(exc)) from exc

    result = ValidatedExtraction(data=data, warnings=warnings, unknown_fields=unknown, confidence_raw=data.confidence)
    haystack = squash(message_text)
    if unknown:
        result.penalise(PENALTY_OFF_CONTRACT, "the reply contained fields outside the contract")

    if data.is_obligation:
        result.context_grounded = is_grounded(data.source_context, haystack, 0.8)
        if data.source_context and not result.context_grounded:
            data.source_context = None
            result.warnings.append("the quoted sentence does not appear in the message and was discarded")
            result.penalise(PENALTY_UNGROUNDED_CONTEXT, "quoted evidence could not be found in the message")
        if not result.context_grounded:
            cap = round(settings.confidence_high - 0.01, 4)
            if data.confidence > cap:
                data.confidence = cap
            result.confidence_notes.append(f"no verifiable supporting quote: confidence capped at {cap:g} so it is reviewed, not auto-created")

        result.deadline_grounded = is_grounded(data.deadline_text, haystack, 0.9)
        if data.deadline_text and not result.deadline_grounded:
            result.warnings.append("the deadline phrase does not appear in the message and was discarded")
            data.deadline_text = None
            result.penalise(PENALTY_UNGROUNDED_DEADLINE, "the deadline wording could not be found in the message")

    if data.counterparty_email:
        email = data.counterparty_email.lower()
        if not (email in message_text.lower() or email == (sender_email or "").lower() or email in {a.lower() for a in known_addresses}):
            result.warnings.append("the counterparty email does not appear in the message and was discarded")
            data.counterparty_email = None
        else:
            data.counterparty_email = email
    before = len(data.entities)
    data.entities = [e for e in data.entities if squash(e.value) in haystack]
    if len(data.entities) != before:
        result.warnings.append(f"{before - len(data.entities)} entity value(s) not found in the message were dropped")
    return result
