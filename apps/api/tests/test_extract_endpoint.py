"""POST /api/internal/extract: what n8n calls to get a validated, analysed proposal (no persistence)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.clock import clock
from app.models import Obligation, Source
from app.services.extraction.llm import LLMBadRequest, LLMNotConfigured, LLMQuotaExhausted, LLMRefused, LLMTimeout, LLMUnavailable
from tests.helpers_extraction import make_extraction, make_message

URL = "/api/internal/extract"
BODY = {"user_email": "alice@example.com", "message": make_message()}


@pytest.fixture(autouse=True)
def _frozen():
    clock.freeze(datetime(2026, 9, 23, 15, 5, tzinfo=UTC))


def test_extract_requires_the_n8n_secret(alice, fake_llm):
    fake_llm(make_extraction())
    assert alice.post(URL, json=BODY).status_code == 401
    assert alice.post(URL, json=BODY, headers={"X-Webhook-Secret": "nope"}).status_code == 401


def test_extract_returns_a_validated_proposal_and_persists_nothing(alice, fake_llm, n8n_headers, db):
    llm = fake_llm(make_extraction())
    r = alice.post(URL, json=BODY, headers=n8n_headers)
    assert r.status_code == 200
    out = r.json()
    assert out["status"] == "OK" and out["retryable"] is False and out["decision"]["action"] == "CREATE"
    assert "error" not in out and out["error_message"] is None  # n8n retries any node whose output item has a json.error, even on HTTP 200
    assert out["extraction"]["title"] == "Submit internship documents" and out["extraction"]["due_at"] is None
    res = out["analysis"]["resolution"]
    assert res["method"] == "RELATIVE" and res["precision"] == "DATETIME" and res["timezone"] == "America/New_York"
    assert out["model"] == "fake-model-1" and out["provider"] == "fake" and out["attempts"] == 1 and out["tokens"] == {"input": 100, "output": 50}
    assert db.scalar(select(func.count()).select_from(Obligation)) == 0 and db.scalar(select(func.count()).select_from(Source)) == 0
    assert len(llm.calls) == 1


@pytest.mark.parametrize(
    "error,http,status,retryable",
    [
        (LLMTimeout("slow"), 503, "LLM_TIMEOUT", True),  # 503 => n8n's HTTP-node retry kicks in
        (LLMUnavailable("down"), 503, "LLM_UNAVAILABLE", True),
        (LLMRefused("declined"), 200, "LLM_REFUSED", False),  # 200 + status => n8n branches, does not blindly retry
        (LLMBadRequest("bad key"), 200, "LLM_REJECTED", False),
        (LLMQuotaExhausted("daily cap", status=429), 200, "LLM_QUOTA_EXHAUSTED", False),  # waiting seconds cannot fix a spent quota
        (LLMNotConfigured("no key"), 200, "NOT_CONFIGURED", False),
    ],
)
def test_transient_llm_failures_are_503_so_n8n_retries_and_permanent_ones_are_200_with_a_status(alice, fake_llm, n8n_headers, error, http, status, retryable):
    fake_llm(error)
    r = alice.post(URL, json=BODY, headers=n8n_headers)
    assert r.status_code == http and r.json()["status"] == status and r.json()["retryable"] is retryable
    assert (r.headers.get("retry-after") == "10") is (http == 503)
    assert r.json()["extraction"] is None and r.json()["error_message"] and "error" not in r.json()


def test_persistently_invalid_output_is_a_200_invalid_output(alice, fake_llm, n8n_headers):
    fake_llm("this is not json")
    r = alice.post(URL, json=BODY, headers=n8n_headers)
    assert r.status_code == 200 and r.json()["status"] == "INVALID_OUTPUT" and r.json()["attempts"] == 2


def test_the_response_never_echoes_the_message_or_secrets(alice, fake_llm, n8n_headers):
    fake_llm(make_extraction())
    blob = alice.post(URL, json=BODY, headers=n8n_headers).text
    assert "Thanks for accepting the offer" not in blob and "finalise onboarding" not in blob
    assert n8n_headers["X-Webhook-Secret"] not in blob


@pytest.mark.parametrize(
    "body,expected",
    [
        ({"user_email": "ghost@example.com", "message": make_message()}, 404),
        ({"user_email": "alice@example.com", "message": make_message(received_at="2026-09-23T15:00:00")}, 422),  # naive timestamp
        ({"user_email": "alice@example.com", "message": make_message(external_id="")}, 422),
        ({"user_email": "alice@example.com", "message": make_message(source_type="CARRIER_PIGEON")}, 422),
        ({"user_email": "alice@example.com", "message": {**make_message(), "extra": 1}}, 422),
        ({"user_email": "not-an-email", "message": make_message()}, 422),
        ({"message": make_message()}, 422),
    ],
)
def test_bad_requests_are_rejected_before_any_llm_call(alice, fake_llm, n8n_headers, body, expected):
    llm = fake_llm(make_extraction())
    assert alice.post(URL, json=body, headers=n8n_headers).status_code == expected
    assert llm.calls == []


def test_a_runaway_workflow_cannot_run_up_an_llm_bill(alice, fake_llm, n8n_headers):
    llm = fake_llm(make_extraction())
    codes = [alice.post(URL, json=BODY, headers=n8n_headers).status_code for _ in range(125)]
    assert codes[:120] == [200] * 120 and 429 in codes[120:]
    assert len(llm.calls) == 120  # the 121st+ never reached the provider


def test_the_extraction_schema_sent_to_the_llm_contains_every_contract_field(alice, fake_llm, n8n_headers):
    llm = fake_llm(make_extraction())
    alice.post(URL, json=BODY, headers=n8n_headers)
    sent = llm.calls[0]["schema"]
    assert "title" in sent["properties"] and sent["additionalProperties"] is False and len(sent["required"]) == 17
    assert json.dumps(sent).count("maxLength") == 0
