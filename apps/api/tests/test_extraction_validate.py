"""Never trust raw LLM output: parsing, normalisation, strict validation, grounding, confidence."""

from __future__ import annotations

import json

import pytest

from app.config import get_settings
from app.services.extraction.schema import LLM_JSON_SCHEMA, LLMExtraction
from app.services.extraction.validate import (
    InvalidOutput,
    is_grounded,
    normalise,
    parse_json_object,
    validate_extraction,
)
from tests.helpers_extraction import BODY, QUOTE, make_extraction

settings = get_settings()


def validate(payload, message=BODY, **kw):
    return validate_extraction(json.dumps(payload) if isinstance(payload, dict) else payload, message, settings, **kw)


# ------------------------------------------------------------------ contract / schema
def test_every_contract_field_is_present_and_required_in_the_schema_sent_to_the_model():
    """Regression: the schema builder once dropped the property literally named 'title'."""
    assert set(LLM_JSON_SCHEMA["properties"]) == set(LLMExtraction.model_fields)
    assert set(LLM_JSON_SCHEMA["required"]) == set(LLMExtraction.model_fields)
    assert LLM_JSON_SCHEMA["additionalProperties"] is False


def test_schema_is_reduced_to_the_subset_providers_accept():
    dumped = json.dumps(LLM_JSON_SCHEMA)
    for forbidden in ("maxLength", "minLength", "minimum", "maximum", "$ref", "$defs", '"default"'):
        assert forbidden not in dumped, forbidden
    assert "DOCUMENT_REQUEST" in dumped and "URGENT" in dumped  # enums survive


# ------------------------------------------------------------------ JSON parsing
@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '  \n{"a": 1}\n ',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        'Sure! Here is the JSON:\n{"a": 1}\nHope that helps.',
        '﻿{"a": 1}',
        '{"a": 1} trailing garbage',
    ],
)
def test_json_is_recovered_from_common_wrappers(raw):
    assert parse_json_object(raw) == {"a": 1}


def test_braces_inside_strings_do_not_confuse_extraction():
    assert parse_json_object('note: {"quote": "use {curly} braces", "n": 2}')["n"] == 2


@pytest.mark.parametrize("raw", ["", "   ", "not json at all", '{"a": 1', '{"a": ', "[1, 2, 3]", '"just a string"', "null", "42"])
def test_unparseable_or_non_object_replies_are_invalid_output(raw):
    with pytest.raises(InvalidOutput):
        parse_json_object(raw)


# ------------------------------------------------------------------ normalisation
def test_unknown_fields_are_dropped_reported_and_penalised():
    v = validate(make_extraction(secret_admin_flag=True, note="hi"))
    assert v.unknown_fields == ["note", "secret_admin_flag"]
    assert any("unexpected field" in w for w in v.warnings)
    assert v.data.confidence == pytest.approx(0.95 * 0.95)
    assert not hasattr(v.data, "secret_admin_flag")


def test_enum_values_are_normalised_but_unknown_values_are_rejected():
    v = validate(make_extraction(obligation_type="document request", priority="high", owner="self"))
    assert v.data.obligation_type.value == "DOCUMENT_REQUEST" and v.data.priority.value == "HIGH" and v.data.owner == "SELF"
    with pytest.raises(InvalidOutput) as exc:
        validate(make_extraction(obligation_type="MEETING"))
    assert "obligation_type" in str(exc.value)


@pytest.mark.parametrize("nullish", ["", "null", "None", "N/A", "unknown", "  ", "-"])
def test_nullish_strings_become_none(nullish):
    v = validate(make_extraction(ambiguity=nullish, counterparty_name=nullish, recurrence=nullish))
    assert v.data.ambiguity is None and v.data.counterparty_name is None and v.data.recurrence is None


@pytest.mark.parametrize(
    "given,expected,warns",
    [(0.94, 0.94, False), ("0.94", 0.94, False), ("94%", 0.94, False), (94, 0.94, True), (1, 1.0, False), (0, 0.0, False)],
)
def test_confidence_formats(given, expected, warns):
    d, w, _ = normalise(make_extraction(confidence=given))
    assert d["confidence"] == pytest.approx(expected)
    assert any("percentage" in x for x in w) == warns


@pytest.mark.parametrize("bad", [1.5, -0.2, 250, "high", None, True])
def test_impossible_confidence_is_invalid_not_clamped(bad):
    with pytest.raises(InvalidOutput):
        validate(make_extraction(confidence=bad))


def test_boolean_strings_and_missing_required_fields():
    assert validate(make_extraction(requires_confirmation="yes", is_obligation="true")).data.requires_confirmation is True
    broken = make_extraction()
    del broken["is_obligation"]
    with pytest.raises(InvalidOutput) as exc:
        validate(broken)
    assert "is_obligation" in str(exc.value)


def test_an_obligation_without_a_title_is_invalid():
    with pytest.raises(InvalidOutput):
        validate(make_extraction(title=None))
    with pytest.raises(InvalidOutput):
        validate(make_extraction(title="   "))
    assert validate(make_extraction(is_obligation=False, title=None, confidence=0.9)).data.is_obligation is False


def test_overlong_free_text_is_shortened_with_a_warning_instead_of_failing_the_whole_extraction():
    v = validate(make_extraction(title="T" * 500, explanation="E" * 900))
    assert len(v.data.title) == 200 and v.data.title.endswith("…") and len(v.data.explanation) == 500
    assert sum("shortened" in w for w in v.warnings) == 2


def test_malformed_entities_are_dropped_and_non_lists_ignored():
    v = validate(make_extraction(entities=[{"type": "org", "value": "University HR"}, {"type": 5}, "junk"]))
    assert [e.value for e in v.data.entities] == ["University HR"] and any("malformed entities" in w for w in v.warnings)
    assert validate(make_extraction(entities="none")).data.entities == []


def test_validation_errors_never_echo_the_offending_value():
    secret = "S3CR3T-EMAIL-CONTENT-4711"
    with pytest.raises(InvalidOutput) as exc:
        validate(make_extraction(obligation_type=secret, priority=secret))
    assert secret not in str(exc.value) and secret not in " ".join(exc.value.reasons)


# ------------------------------------------------------------------ grounding
def test_a_verbatim_quote_and_deadline_phrase_are_grounded_and_keep_full_confidence():
    v = validate(make_extraction())
    assert v.context_grounded and v.deadline_grounded and v.data.confidence == 0.95 and v.confidence_notes == []


def test_grounding_tolerates_case_whitespace_and_punctuation_but_not_invention():
    hay = "please submit your signed documents by friday 5pm thanks dana"
    assert is_grounded("Please  submit your signed documents, by Friday 5pm!", hay, 0.8)
    assert not is_grounded("Kindly upload the tax return before midnight on Monday", hay, 0.8)
    assert not is_grounded("", hay, 0.8) and not is_grounded(None, hay, 0.8)


def test_an_invented_quote_is_discarded_penalised_and_can_no_longer_be_auto_created():
    v = validate(make_extraction(source_context="Please wire $5,000 to account 123 immediately", confidence=0.99))
    assert v.data.source_context is None and not v.context_grounded
    assert v.data.confidence < settings.confidence_high  # capped: cannot reach the auto-create threshold
    assert any("quoted evidence" in n for n in v.confidence_notes) and any("capped" in n for n in v.confidence_notes)
    assert any("does not appear in the message" in w for w in v.warnings)


def test_no_quote_at_all_is_capped_below_the_auto_create_threshold():
    v = validate(make_extraction(source_context=None, confidence=0.99))
    assert v.data.confidence == pytest.approx(settings.confidence_high - 0.01)
    assert v.data.confidence < settings.confidence_high


def test_an_invented_deadline_phrase_is_discarded_and_penalised():
    v = validate(make_extraction(deadline_text="by the end of next quarter", confidence=0.9))
    assert v.data.deadline_text is None and not v.deadline_grounded
    assert v.data.confidence == pytest.approx(0.9 * 0.8) and any("deadline wording" in n for n in v.confidence_notes)


def test_an_invented_counterparty_email_is_dropped_but_the_senders_own_is_accepted():
    assert validate(make_extraction(counterparty_email="ceo@evil.example.net")).data.counterparty_email is None
    assert validate(make_extraction(counterparty_email="HR@Example.org"), sender_email="hr@example.org").data.counterparty_email == "hr@example.org"
    body_with_email = BODY + "\nReply to onboarding@example.org"
    assert validate(make_extraction(counterparty_email="Onboarding@Example.org"), message=body_with_email).data.counterparty_email == "onboarding@example.org"


def test_an_address_from_the_messages_own_headers_is_accepted_but_an_unrelated_one_is_not():
    """Recipients of a message the person sent are header data, not part of the text a model reads."""
    v = validate(make_extraction(counterparty_email="Marcus@Example.org"), known_addresses=["marcus@example.org", "other@example.org"])
    assert v.data.counterparty_email == "marcus@example.org"
    v = validate(make_extraction(counterparty_email="ceo@evil.example.net"), known_addresses=["marcus@example.org"])
    assert v.data.counterparty_email is None and any("counterparty email" in w for w in v.warnings)


def test_hallucinated_entities_are_dropped():
    v = validate(make_extraction(entities=[{"type": "org", "value": "University HR"}, {"type": "org", "value": "Acme Offshore Holdings"}]))
    assert [e.value for e in v.data.entities] == ["University HR"]


def test_a_non_obligation_is_neither_capped_nor_penalised_for_lacking_a_quote():
    v = validate(make_extraction(is_obligation=False, title=None, source_context=None, deadline_text=None, confidence=0.97))
    assert v.data.confidence == 0.97 and v.confidence_notes == []


def test_revalidating_an_already_validated_result_is_idempotent():
    """The commit step re-validates what /extract returned; it must not change the outcome or double-penalise."""
    first = validate(make_extraction(source_context="an invented sentence", confidence=0.9, note="x"))
    again = validate(first.data.model_dump(mode="json"))
    assert again.data.confidence == pytest.approx(first.data.confidence)
    assert again.data.source_context is None and again.data.deadline_text == first.data.deadline_text


def test_json_reply_with_fence_and_chatter_still_validates():
    raw = "Here you go:\n```json\n" + json.dumps(make_extraction()) + "\n```\n"
    assert validate(raw).data.title == "Submit internship documents" and QUOTE in BODY
