"""LLM provider adapters behind one interface.

Every adapter returns the model's raw reply text (validation happens elsewhere, never here) and maps
every failure to one of a few typed errors, so the caller's policy is simple and identical for all
providers:

    LLMNotConfigured  no key / provider disabled                     -> not retryable
    LLMBadRequest     bad key / unknown model / rejected request     -> not retryable (retrying cannot help)
    LLMRefused        the provider's safety system declined          -> not retryable
    LLMUnavailable    timeout, 429, 5xx, connection error            -> retryable (already retried a bounded number of times)
    LLMQuotaExhausted the provider's QUOTA is used up (not a momentary rate limit) -> not retryable: waiting seconds cannot help

Nothing here logs prompts, message content or keys.
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import httpx

from app.config import Settings, get_settings
from app.logging_config import redact

log = logging.getLogger("commitmentos.llm")

Message = dict[str, str]  # {"role": "user" | "assistant", "content": "..."}


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    stop_reason: str  # "end_turn" | "max_tokens"
    input_tokens: int | None = None
    output_tokens: int | None = None
    request_id: str | None = None
    latency_ms: int = 0


class LLMError(Exception):
    code = "LLM_ERROR"
    retryable = False

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(redact(message))
        self.status = status


class LLMNotConfigured(LLMError):
    code = "NOT_CONFIGURED"


class LLMBadRequest(LLMError):
    code = "LLM_REJECTED"


class LLMRefused(LLMError):
    code = "LLM_REFUSED"


class LLMUnavailable(LLMError):
    code = "LLM_UNAVAILABLE"
    retryable = True


class LLMTimeout(LLMUnavailable):
    code = "LLM_TIMEOUT"


class LLMQuotaExhausted(LLMUnavailable):
    """A spent quota (daily cap, exhausted credits) rather than a momentary rate limit. Retrying only burns calls."""

    code = "LLM_QUOTA_EXHAUSTED"
    retryable = False


class LLMClient(Protocol):
    provider: str
    model: str

    def complete_json(
        self, *, system: str, messages: list[Message], schema: dict[str, Any], max_output_tokens: int
    ) -> LLMResponse: ...


# --------------------------------------------------------------------------------------- disabled
class NotConfiguredClient:
    provider = "none"

    def __init__(self, reason: str, provider: str = "none") -> None:
        self.provider = provider
        self.model = "-"
        self._reason = reason

    def complete_json(self, **_: Any) -> LLMResponse:
        raise LLMNotConfigured(self._reason)


# --------------------------------------------------------------------------------------- shared HTTP
def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
        err = body.get("error", body) if isinstance(body, dict) else body
        if isinstance(err, dict):
            return str(err.get("message") or err)[:300]
        return str(err)[:300]
    except ValueError:
        return response.text[:200]


def _exhausted_quota(response: httpx.Response) -> str | None:
    """Name the exhausted quota if this 429 is a spent quota rather than a per-minute rate limit, else None.

    Gemini lists the quota in ``error.details[].violations[].quotaId`` (``...PerDay...`` is a daily cap), OpenAI-style
    APIs use ``insufficient_quota``. The provider's own retry hint on these is a few seconds, which is misleading.
    """
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None
    if "insufficient_quota" in (str(error.get("code")), str(error.get("type"))):
        return "insufficient_quota"
    for detail in error.get("details") or []:
        for violation in (detail.get("violations") or []) if isinstance(detail, dict) else []:
            quota_id = str(violation.get("quotaId", "")) if isinstance(violation, dict) else ""
            if "perday" in quota_id.lower():
                return quota_id
    return None


class _HttpPoster:
    """POST JSON with a bounded, backoff-and-jitter retry on transient failures only."""

    def __init__(
        self,
        *,
        timeout: float,
        max_retries: int,
        sleeper: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self._sleep = sleeper
        self._transport = transport

    def post(self, url: str, *, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        attempt = 0
        while True:
            retry_after: float | None = None
            try:
                with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                    response = client.post(url, headers=headers, json=body)
            except httpx.TimeoutException:
                error: LLMError = LLMTimeout(f"The LLM did not answer within {self.timeout:g}s")
            except httpx.HTTPError:
                error = LLMUnavailable("Could not reach the LLM service")
            else:
                if response.status_code < 400:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise LLMUnavailable("The LLM returned a non-JSON response") from exc
                if response.status_code == 429 and (quota := _exhausted_quota(response)):
                    raise LLMQuotaExhausted(
                        f"The LLM provider's quota is exhausted ({quota}); retrying will not help until it resets or the plan changes",
                        status=429,
                    )
                if response.status_code in (408, 409, 425, 429) or response.status_code >= 500:
                    error = LLMUnavailable(f"The LLM service returned {response.status_code}", status=response.status_code)
                    header = response.headers.get("retry-after", "")
                    retry_after = min(float(header), 30.0) if header.replace(".", "", 1).isdigit() else None
                else:
                    raise LLMBadRequest(
                        f"The LLM rejected the request ({response.status_code}): {_error_message(response)}",
                        status=response.status_code,
                    )
            if attempt >= self.max_retries:
                raise error
            delay = retry_after if retry_after is not None else min(2**attempt + random.uniform(0, 0.5), 20.0)  # noqa: S311
            log.warning("LLM call failed (%s, HTTP %s); retry %d/%d in %.1fs", error.code, error.status or "-", attempt + 1, self.max_retries, delay)
            self._sleep(delay)
            attempt += 1


# --------------------------------------------------------------------------------------- Gemini
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_GEMINI_BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY"}


class GeminiClient:
    """Native Gemini REST. Auth via the ``x-goog-api-key`` header (never the URL, which would end up in logs)."""

    provider = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        thinking_budget: int | None,
        timeout: float,
        max_retries: int,
        base_url: str = _GEMINI_BASE,
        sleeper: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self._key = api_key
        self._budget = thinking_budget
        self._base = base_url.rstrip("/")
        self._http = _HttpPoster(timeout=timeout, max_retries=max_retries, sleeper=sleeper, transport=transport)

    def _thinking_config(self) -> dict[str, Any] | None:
        if self._budget is None or not re.search(r"gemini-(2\.5|3)|-latest$", self.model):
            return None  # blank setting, or a model family that rejects thinkingConfig
        budget = self._budget
        if "pro" in self.model and budget == 0:
            budget = 128  # Pro models cannot switch thinking off entirely
        return {"thinkingBudget": budget}

    def complete_json(
        self, *, system: str, messages: list[Message], schema: dict[str, Any], max_output_tokens: int
    ) -> LLMResponse:
        generation: dict[str, Any] = {
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,  # the JSON-Schema field; the older `responseSchema` rejects additionalProperties
            "temperature": 0,
            "maxOutputTokens": max_output_tokens,
        }
        thinking = self._thinking_config()
        if thinking is not None:
            generation["thinkingConfig"] = thinking
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [
                {"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]} for m in messages
            ],
            "generationConfig": generation,
        }
        started = time.perf_counter()
        data = self._http.post(
            f"{self._base}/models/{self.model}:generateContent",
            headers={"x-goog-api-key": self._key, "Content-Type": "application/json"},
            body=body,
        )
        block = (data.get("promptFeedback") or {}).get("blockReason")
        if block:
            raise LLMRefused(f"The prompt was blocked by the provider ({block})")
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMUnavailable("The LLM returned no candidates")
        candidate = candidates[0]
        finish = candidate.get("finishReason")
        if finish in _GEMINI_BLOCKED:
            raise LLMRefused(f"The reply was blocked by the provider ({finish})")
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        usage = data.get("usageMetadata") or {}
        return LLMResponse(
            text=text,
            provider=self.provider,
            model=data.get("modelVersion") or self.model,
            stop_reason="max_tokens" if finish == "MAX_TOKENS" else "end_turn",
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            request_id=data.get("responseId"),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


# --------------------------------------------------------------------------------------- OpenAI-compatible
class OpenAICompatClient:
    """Any server exposing /chat/completions with json_schema response_format (Ollama, vLLM, LM Studio, OpenAI)."""

    provider = "openai_compat"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout: float,
        max_retries: int,
        sleeper: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.model = model
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._key = api_key
        self._http = _HttpPoster(timeout=timeout, max_retries=max_retries, sleeper=sleeper, transport=transport)

    def complete_json(
        self, *, system: str, messages: list[Message], schema: dict[str, Any], max_output_tokens: int
    ) -> LLMResponse:
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": 0,
            "max_tokens": max_output_tokens,
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "commitment_extraction", "strict": True, "schema": schema},
            },
        }
        started = time.perf_counter()
        data = self._http.post(self._url, headers=headers, body=body)
        choices = data.get("choices") or []
        if not choices:
            raise LLMUnavailable("The LLM returned no choices")
        choice = choices[0]
        finish = choice.get("finish_reason")
        if finish == "content_filter":
            raise LLMRefused("The reply was blocked by the provider's content filter")
        usage = data.get("usage") or {}
        return LLMResponse(
            text=(choice.get("message") or {}).get("content") or "",
            provider=self.provider,
            model=data.get("model") or self.model,
            stop_reason="max_tokens" if finish == "length" else "end_turn",
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            request_id=data.get("id"),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


# --------------------------------------------------------------------------------------- factory
def build_llm_client(settings: Settings) -> LLMClient:
    provider = settings.llm_provider
    if provider == "none":
        return NotConfiguredClient("LLM extraction is disabled (LLM_PROVIDER=none)")
    if provider == "gemini":
        key = settings.gemini_api_key.get_secret_value()
        if not key:
            return NotConfiguredClient("GEMINI_API_KEY is not set", "gemini")
        return GeminiClient(
            api_key=key,
            model=settings.gemini_model,
            thinking_budget=settings.gemini_thinking_budget,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_transient_retries,
        )
    return OpenAICompatClient(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key.get_secret_value(),
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_transient_retries,
    )


@lru_cache(maxsize=1)
def _cached_client() -> LLMClient:
    return build_llm_client(get_settings())


def get_llm_client() -> LLMClient:
    """FastAPI dependency (overridden in tests)."""
    return _cached_client()
