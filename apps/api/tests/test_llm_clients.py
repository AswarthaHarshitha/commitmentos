"""LLM provider adapters against mocked transports: request shape, failure mapping, bounded retries, key hygiene."""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.services.extraction.llm import (
    GeminiClient,
    LLMBadRequest,
    LLMQuotaExhausted,
    LLMRefused,
    LLMTimeout,
    LLMUnavailable,
    NotConfiguredClient,
    OpenAICompatClient,
    build_llm_client,
)
from app.services.extraction.schema import LLM_JSON_SCHEMA

KEY = "AQ.test-key-1234567890-SHOULD-NEVER-LEAK"
MESSAGES = [{"role": "user", "content": "the email"}]
COMMON = dict(system="SYS", messages=MESSAGES, schema=LLM_JSON_SCHEMA, max_output_tokens=1500)


def settings_for(**env) -> Settings:
    return Settings(_env_file=None, jwt_secret="x" * 40, n8n_inbound_secret="y" * 30, n8n_outbound_secret="z" * 30, **env)


# ===================================================================================== Gemini
def gemini(handler, *, model="gemini-2.5-flash", retries=2, budget=0):
    sleeps: list[float] = []
    client = GeminiClient(api_key=KEY, model=model, thinking_budget=budget, timeout=5, max_retries=retries,
                          sleeper=sleeps.append, transport=httpx.MockTransport(handler))
    return client, sleeps


def ok_gemini(text='{"a": 1}', finish="STOP", extra=None):
    body = {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}],
            "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 7}, "modelVersion": "gemini-2.5-flash", "responseId": "r1"}
    body.update(extra or {})
    return httpx.Response(200, json=body)


def test_gemini_request_shape_and_key_hygiene():
    seen = {}

    def handler(request: httpx.Request):
        seen.update(url=str(request.url), headers=dict(request.headers), body=json.loads(request.content))
        return ok_gemini()

    r = gemini(handler)[0].complete_json(**COMMON)
    assert r.text == '{"a": 1}' and r.stop_reason == "end_turn" and (r.input_tokens, r.output_tokens) == (11, 7) and r.request_id == "r1"
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    assert seen["headers"]["x-goog-api-key"] == KEY
    assert KEY not in seen["url"] and KEY not in json.dumps(seen["body"])  # header auth only: never in a URL that would be logged
    b = seen["body"]
    assert b["systemInstruction"]["parts"][0]["text"] == "SYS" and b["contents"] == [{"role": "user", "parts": [{"text": "the email"}]}]
    g = b["generationConfig"]
    assert g["responseMimeType"] == "application/json" and g["responseJsonSchema"] == LLM_JSON_SCHEMA and "responseSchema" not in g
    assert g["temperature"] == 0 and g["maxOutputTokens"] == 1500 and g["thinkingConfig"] == {"thinkingBudget": 0}


def test_gemini_assistant_turns_are_sent_as_model_role_for_the_repair_loop():
    seen = {}
    gemini(lambda req: (seen.update(b=json.loads(req.content)), ok_gemini())[1])[0].complete_json(
        system="S", messages=[{"role": "user", "content": "a"}, {"role": "assistant", "content": "bad"}, {"role": "user", "content": "fix"}],
        schema=LLM_JSON_SCHEMA, max_output_tokens=100)
    assert [c["role"] for c in seen["b"]["contents"]] == ["user", "model", "user"]


@pytest.mark.parametrize(
    "model,budget,expected",
    [("gemini-2.5-flash", 0, {"thinkingBudget": 0}), ("gemini-2.5-pro", 0, {"thinkingBudget": 128}), ("gemini-flash-latest", 0, {"thinkingBudget": 0}),
     ("gemini-2.5-flash", 512, {"thinkingBudget": 512}), ("gemini-2.0-flash", 0, None),
     ("gemini-3-flash-preview", None, None)],  # blank GEMINI_THINKING_BUDGET: send nothing (some models reject thinkingConfig)
)
def test_gemini_thinking_config_only_where_the_model_supports_it(model, budget, expected):
    seen = {}
    gemini(lambda req: (seen.update(b=json.loads(req.content)), ok_gemini())[1], model=model, budget=budget)[0].complete_json(**COMMON)
    assert seen["b"]["generationConfig"].get("thinkingConfig") == expected


def test_gemini_ignores_thought_parts_and_reports_truncation():
    body = {"candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True}, {"text": '{"a": '}]}, "finishReason": "MAX_TOKENS"}]}
    r = gemini(lambda req: httpx.Response(200, json=body))[0].complete_json(**COMMON)
    assert r.text == '{"a": ' and r.stop_reason == "max_tokens"


@pytest.mark.parametrize("payload", [
    {"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]},
    {"candidates": [{"finishReason": "PROHIBITED_CONTENT"}]},
    {"promptFeedback": {"blockReason": "OTHER"}, "candidates": []},
])
def test_gemini_safety_blocks_are_refusals_not_retried(payload):
    calls = []
    client, _ = gemini(lambda req: (calls.append(1), httpx.Response(200, json=payload))[1])
    with pytest.raises(LLMRefused):
        client.complete_json(**COMMON)
    assert len(calls) == 1


def test_gemini_empty_candidates_is_unavailable():
    with pytest.raises(LLMUnavailable):
        gemini(lambda req: httpx.Response(200, json={"candidates": []}))[0].complete_json(**COMMON)


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_gemini_client_errors_are_rejections_and_are_never_retried(status):
    calls = []
    client, sleeps = gemini(lambda req: (calls.append(1), httpx.Response(status, json={"error": {"status": "INVALID_ARGUMENT", "message": "API key not valid"}}))[1])
    with pytest.raises(LLMBadRequest) as exc:
        client.complete_json(**COMMON)
    assert len(calls) == 1 and sleeps == [] and exc.value.status == status and "API key not valid" in str(exc.value) and KEY not in str(exc.value)


def test_gemini_429_honours_retry_after_then_succeeds():
    replies = [httpx.Response(429, headers={"retry-after": "3"}, json={}), ok_gemini()]
    client, sleeps = gemini(lambda req: replies.pop(0))
    assert client.complete_json(**COMMON).text == '{"a": 1}'
    assert sleeps == [3.0]


# The body Google really returns when the FREE-TIER DAILY cap is spent (captured from a live 429). Note its misleading "retryDelay: 39s".
DAILY_QUOTA_429 = {"error": {
    "code": 429, "status": "RESOURCE_EXHAUSTED",
    "message": "You exceeded your current quota, please check your plan and billing details.\nPlease retry in 39.3s.",
    "details": [
        {"@type": "type.googleapis.com/google.rpc.Help", "links": [{"url": "https://ai.google.dev/gemini-api/docs/rate-limits"}]},
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{
            "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
            "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", "quotaDimensions": {"model": "gemini-2.5-flash"}, "quotaValue": "20"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "39s"},
    ]}}


def test_gemini_daily_quota_is_reported_as_such_and_never_retried():
    calls = []
    client, sleeps = gemini(lambda req: (calls.append(1), httpx.Response(429, headers={"retry-after": "39"}, json=DAILY_QUOTA_429))[1], retries=3)
    with pytest.raises(LLMQuotaExhausted) as exc:
        client.complete_json(**COMMON)
    assert len(calls) == 1 and sleeps == []  # retrying a spent daily cap only burns calls
    assert exc.value.code == "LLM_QUOTA_EXHAUSTED" and exc.value.retryable is False and exc.value.status == 429
    assert "GenerateRequestsPerDayPerProjectPerModel-FreeTier" in str(exc.value) and "39" not in str(exc.value)  # names the quota, drops the misleading hint


def test_gemini_per_minute_rate_limit_is_still_retried():
    minute = {"error": {"status": "RESOURCE_EXHAUSTED", "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
        {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}]}}
    replies = [httpx.Response(429, json=minute), ok_gemini()]
    client, sleeps = gemini(lambda req: replies.pop(0))
    assert client.complete_json(**COMMON).text == '{"a": 1}' and len(sleeps) == 1


@pytest.mark.parametrize("body", [None, [], {"error": "nope"}, {"error": {"details": "x"}}, {"error": {"details": [None, {"violations": [3, {"quotaId": None}]}]}}])
def test_gemini_429_with_unexpected_bodies_is_treated_as_a_transient_rate_limit(body):
    calls = []
    client, _ = gemini(lambda req: (calls.append(1), httpx.Response(429, json=body))[1], retries=1)
    with pytest.raises(LLMUnavailable) as exc:
        client.complete_json(**COMMON)
    assert not isinstance(exc.value, LLMQuotaExhausted) and len(calls) == 2


def test_retry_warnings_carry_the_http_status(caplog):
    client, _ = gemini(lambda req: httpx.Response(503, json={}), retries=1)
    with caplog.at_level("WARNING", logger="commitmentos.llm"), pytest.raises(LLMUnavailable):
        client.complete_json(**COMMON)
    warning = next(r for r in caplog.records if "retry" in r.getMessage())
    assert "HTTP 503" in warning.getMessage() and KEY not in caplog.text


def test_gemini_retry_after_is_capped():
    replies = [httpx.Response(429, headers={"retry-after": "9999"}, json={}), ok_gemini()]
    client, sleeps = gemini(lambda req: replies.pop(0))
    client.complete_json(**COMMON)
    assert sleeps == [30.0]


def test_gemini_transient_failures_are_retried_a_bounded_number_of_times():
    calls = []
    client, sleeps = gemini(lambda req: (calls.append(1), httpx.Response(503, json={}))[1], retries=2)
    with pytest.raises(LLMUnavailable) as exc:
        client.complete_json(**COMMON)
    assert len(calls) == 3 and len(sleeps) == 2 and exc.value.status == 503 and exc.value.retryable  # 1 try + 2 retries, then it gives up
    assert 1 <= sleeps[0] <= 1.6 and 2 <= sleeps[1] <= 2.6  # exponential backoff with jitter


def test_gemini_timeouts_and_connection_errors():
    def slow(req):
        raise httpx.ReadTimeout("slow")

    def down(req):
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMTimeout):
        gemini(slow, retries=1)[0].complete_json(**COMMON)
    with pytest.raises(LLMUnavailable) as exc:
        gemini(down, retries=0)[0].complete_json(**COMMON)
    assert not isinstance(exc.value, LLMTimeout)


def test_gemini_non_json_success_body_is_unavailable():
    with pytest.raises(LLMUnavailable):
        gemini(lambda req: httpx.Response(200, content=b"<html>gateway</html>"), retries=0)[0].complete_json(**COMMON)


# ===================================================================================== OpenAI-compatible
def compat(handler, *, key="", retries=1):
    sleeps: list[float] = []
    return OpenAICompatClient(base_url="http://ollama:11434/v1/", model="qwen2.5:3b", api_key=key, timeout=5, max_retries=retries,
                              sleeper=sleeps.append, transport=httpx.MockTransport(handler)), sleeps


def ok_compat(content='{"a": 1}', finish="stop"):
    return httpx.Response(200, json={"id": "c1", "model": "qwen2.5:3b", "choices": [{"message": {"content": content}, "finish_reason": finish}],
                                      "usage": {"prompt_tokens": 9, "completion_tokens": 4}})


def test_openai_compat_request_shape():
    seen = {}

    def handler(req):
        seen.update(url=str(req.url), auth=req.headers.get("authorization"), body=json.loads(req.content))
        return ok_compat()

    r = compat(handler)[0].complete_json(**COMMON)
    assert r.text == '{"a": 1}' and (r.input_tokens, r.output_tokens) == (9, 4) and r.stop_reason == "end_turn"
    assert seen["url"] == "http://ollama:11434/v1/chat/completions" and seen["auth"] is None  # no key -> no Authorization header
    b = seen["body"]
    assert b["messages"][0] == {"role": "system", "content": "SYS"} and b["messages"][1]["content"] == "the email"
    assert b["response_format"]["type"] == "json_schema" and b["response_format"]["json_schema"]["strict"] is True
    assert b["response_format"]["json_schema"]["schema"] == LLM_JSON_SCHEMA and b["temperature"] == 0 and b["stream"] is False


def test_openai_compat_sends_the_bearer_key_only_when_configured():
    seen = {}
    compat(lambda req: (seen.update(a=req.headers.get("authorization")), ok_compat())[1], key="sk-local-1234567890")[0].complete_json(**COMMON)
    assert seen["a"] == "Bearer sk-local-1234567890"


def test_openai_compat_finish_reasons_and_errors():
    assert compat(lambda req: ok_compat(finish="length"))[0].complete_json(**COMMON).stop_reason == "max_tokens"
    with pytest.raises(LLMRefused):
        compat(lambda req: ok_compat(finish="content_filter"))[0].complete_json(**COMMON)
    with pytest.raises(LLMBadRequest):
        compat(lambda req: httpx.Response(401, json={"error": {"message": "bad key"}}))[0].complete_json(**COMMON)
    with pytest.raises(LLMUnavailable):
        compat(lambda req: httpx.Response(200, json={"choices": []}))[0].complete_json(**COMMON)
    calls = []
    with pytest.raises(LLMUnavailable):
        compat(lambda req: (calls.append(1), httpx.Response(500, json={}))[1], retries=1)[0].complete_json(**COMMON)
    assert len(calls) == 2


def test_openai_compat_spent_credits_are_a_quota_error_not_a_retry_loop():
    calls = []
    body = {"error": {"message": "You exceeded your current quota", "type": "insufficient_quota", "code": "insufficient_quota"}}
    client, sleeps = compat(lambda req: (calls.append(1), httpx.Response(429, json=body))[1], retries=3)
    with pytest.raises(LLMQuotaExhausted) as exc:
        client.complete_json(**COMMON)
    assert len(calls) == 1 and sleeps == [] and "insufficient_quota" in str(exc.value)


# ===================================================================================== factory
def test_factory_builds_the_configured_provider():
    assert isinstance(build_llm_client(settings_for(llm_provider="gemini", gemini_api_key=KEY)), GeminiClient)
    compat_client = build_llm_client(settings_for(llm_provider="openai_compat", llm_base_url="http://x/v1", llm_model="m"))
    assert isinstance(compat_client, OpenAICompatClient) and compat_client.model == "m"


@pytest.mark.parametrize("provider,reason", [("none", "disabled"), ("gemini", "GEMINI_API_KEY")])
def test_missing_configuration_yields_a_client_that_fails_clearly_instead_of_crashing_at_boot(provider, reason):
    client = build_llm_client(settings_for(llm_provider=provider))
    assert isinstance(client, NotConfiguredClient)
    from app.services.extraction.llm import LLMNotConfigured

    with pytest.raises(LLMNotConfigured) as exc:
        client.complete_json(**COMMON)
    assert reason in str(exc.value)


def test_the_default_provider_is_gemini_and_the_supported_set_is_closed():
    assert Settings.model_fields["llm_provider"].default == "gemini"
    with pytest.raises(ValueError):
        settings_for(llm_provider="some-other-provider")


@pytest.mark.parametrize("raw,expected", [("0", 0), ("512", 512), ("", None), ("  ", None)])
def test_a_blank_gemini_thinking_budget_means_send_no_thinking_config_and_reaches_the_client(raw, expected):
    s = settings_for(gemini_thinking_budget=raw)
    assert s.gemini_thinking_budget == expected
    client = build_llm_client(settings_for(llm_provider="gemini", gemini_api_key=KEY, gemini_model="gemini-2.5-flash", gemini_thinking_budget=raw))
    assert client._thinking_config() == (None if expected is None else {"thinkingBudget": expected})


def test_the_test_environment_never_holds_real_llm_credentials():
    """Guard for the suite itself: conftest blanks provider keys so no test can reach a real LLM by accident."""
    import os

    assert not os.environ.get("GEMINI_API_KEY") and not os.environ.get("LLM_API_KEY")
    from app.config import get_settings

    assert get_settings().llm_provider == "none"
