"""One OpenAI-compatible chat client, shared by every provider in the registry.

Providers differ only in base URL, model id and quirks, all of which the
registry already carries, so there is exactly one HTTP path to test and one
place where retries and redaction happen.
"""

import random
import time
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit, urlunsplit

import httpx

from buildsleuth.llm.rate_limit import RateLimiter
from buildsleuth.llm.registry import ModelSpec
from buildsleuth.llm.types import (
    CompletionRequest,
    CompletionResult,
    ModelError,
    QuotaExhaustedError,
    Usage,
)

CHAT_COMPLETIONS_PATH = "/chat/completions"
# Triage is a batch operation over long logs, and reasoning models can take a
# while on a large context. Nothing here is interactive, so wait generously.
DEFAULT_TIMEOUT_SECONDS = 180.0
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_MULTIPLIER = 2.0
BACKOFF_MAX_SECONDS = 30.0
JITTER_FRACTION = 0.25
MILLISECONDS_PER_SECOND = 1000.0
ERROR_PREVIEW_CHARS = 200
REDACTED = "[redacted]"
SCHEMA_NAME = "triage"
JSON_OBJECT_FORMAT: dict[str, Any] = {"type": "json_object"}

RETRYABLE_STATUSES = frozenset(
    {
        httpx.codes.TOO_MANY_REQUESTS,
        httpx.codes.INTERNAL_SERVER_ERROR,
        httpx.codes.BAD_GATEWAY,
        httpx.codes.SERVICE_UNAVAILABLE,
        httpx.codes.GATEWAY_TIMEOUT,
    }
)

# A 429 can mean "slow down" or "your daily allowance is gone". Retrying the
# second kind cannot succeed and burns three more requests against the very
# quota that is already exhausted, so it fails fast instead.
_QUOTA_EXHAUSTED_MARKERS = ("exceeded your current quota", "quota_exceeded", "billing details")


def _is_quota_exhausted(response: httpx.Response) -> bool:
    if response.status_code != httpx.codes.TOO_MANY_REQUESTS:
        return False
    body = response.text.lower()
    return any(marker in body for marker in _QUOTA_EXHAUSTED_MARKERS)


def _redact_url(url: str) -> str:
    """Keep scheme, host and path only, so a key carried in a query never leaks."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class OpenAICompatClient:
    """Calls `{base_url}/chat/completions` for one model spec."""

    def __init__(
        self,
        spec: ModelSpec,
        api_key: str | None = None,
        *,
        rate_limiter: RateLimiter | None = None,
        http: httpx.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._spec = spec
        self._api_key = api_key
        self._rate_limiter = rate_limiter
        self._clock = clock
        self._sleep = sleep
        self._url = spec.base_url.rstrip("/") + CHAT_COMPLETIONS_PATH
        self._owns_http = http is None
        self._http = http if http is not None else httpx.Client(timeout=timeout)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the HTTP pool, unless the caller supplied its own client."""
        if self._owns_http:
            self._http.close()

    def complete(self, request: CompletionRequest) -> CompletionResult:
        """Run one chat completion, honouring the rate limiter and retry policy."""
        payload = self._build_payload(request)
        if self._rate_limiter is not None:
            self._rate_limiter.acquire()
        # Includes retry waits on purpose: that is the latency a caller feels.
        started = self._clock()
        response = self._post_with_retries(payload)
        latency_ms = (self._clock() - started) * MILLISECONDS_PER_SECOND
        result = self._parse(response, latency_ms)
        if self._rate_limiter is not None:
            self._rate_limiter.record(result.usage)
        return result

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _build_payload(self, request: CompletionRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._spec.api_model,
            "messages": [
                {"role": message.role.value, "content": message.content}
                for message in request.messages
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.response_schema is not None:
            payload["response_format"] = self._response_format(request.response_schema)
        return payload

    def _response_format(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Strict schema where the provider enforces one, plain JSON mode otherwise.

        Under plain JSON mode the provider only guarantees valid JSON, so the
        caller is the one that has to put the schema in the prompt.
        """
        if not self._spec.quirks.native_json_schema:
            return JSON_OBJECT_FORMAT
        return {
            "type": "json_schema",
            "json_schema": {"name": SCHEMA_NAME, "strict": True, "schema": schema},
        }

    def _post_with_retries(self, payload: dict[str, Any]) -> httpx.Response:
        attempts = self._spec.quirks.max_retries + 1
        for attempt in range(attempts):
            last_attempt = attempt == attempts - 1
            try:
                response = self._http.post(self._url, json=payload, headers=self._headers())
            except httpx.TimeoutException as error:
                # A timeout is transient. Dropping the case here would silently
                # shrink the evaluated set, so retry before giving up.
                if last_attempt:
                    raise ModelError(
                        f"{self._spec.name}: timed out after {attempts} attempts"
                    ) from error
                self._sleep(self._backoff(attempt))
                continue

            if response.is_success:
                return response
            if _is_quota_exhausted(response):
                raise QuotaExhaustedError(
                    f"{self._spec.name}: {self._spec.provider.value} daily quota is exhausted."
                    " Wait for it to reset or pick another model."
                )
            if response.status_code not in RETRYABLE_STATUSES or last_attempt:
                raise self._error(response)
            self._sleep(self._retry_delay(attempt, response))
        raise ModelError(f"{self._spec.name}: no attempt was made against {self._url}")

    def _retry_delay(self, attempt: int, response: httpx.Response) -> float:
        """Server-stated wait when there is one, jittered exponential backoff otherwise."""
        stated = _retry_after_seconds(response)
        return stated if stated is not None else self._backoff(attempt)

    def _backoff(self, attempt: int) -> float:
        delay = min(BACKOFF_BASE_SECONDS * (BACKOFF_MULTIPLIER**attempt), BACKOFF_MAX_SECONDS)
        return delay + random.uniform(0.0, JITTER_FRACTION * delay)

    def _error(self, response: httpx.Response) -> ModelError:
        detail = self._redact(response.text[:ERROR_PREVIEW_CHARS])
        return ModelError(
            f"{self._spec.name}: {self._spec.provider.value} returned "
            f"{response.status_code} for {_redact_url(self._url)}: {detail}"
        )

    def _redact(self, text: str) -> str:
        """Strip the API key from anything we are about to raise or log."""
        if not self._api_key:
            return text
        return text.replace(self._api_key, REDACTED)

    def _parse(self, response: httpx.Response, latency_ms: float) -> CompletionResult:
        try:
            body: dict[str, Any] = response.json()
        except ValueError as exc:
            raise ModelError(
                f"{self._spec.name}: response from {_redact_url(self._url)} was not JSON"
            ) from exc
        choices = body.get("choices") or []
        if not choices:
            raise ModelError(f"{self._spec.name}: response carried no choices")
        content = choices[0].get("message", {}).get("content") or ""
        raw_usage = body.get("usage") or {}
        return CompletionResult(
            content=content,
            usage=Usage(
                input_tokens=int(raw_usage.get("prompt_tokens") or 0),
                output_tokens=int(raw_usage.get("completion_tokens") or 0),
            ),
            latency_ms=latency_ms,
            model=self._spec.name,
        )


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Parse a numeric Retry-After, ignoring the HTTP-date form we never see."""
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None
    if seconds < 0.0:
        return None
    return min(seconds, BACKOFF_MAX_SECONDS)
