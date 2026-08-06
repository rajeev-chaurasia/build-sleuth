import dataclasses
import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from buildsleuth.llm.client import OpenAICompatClient
from buildsleuth.llm.rate_limit import RateLimiter
from buildsleuth.llm.registry import ModelSpec, Provider, ProviderQuirks, get_model_spec
from buildsleuth.llm.types import CompletionRequest, Message, ModelError, Role, Usage

API_KEY = "sk-not-a-real-key-0001"
GEMINI = get_model_spec("gemini-3.6-flash")
GROQ = get_model_spec("groq-llama-3.3-70b")
GEMINI_URL = f"{GEMINI.base_url}/chat/completions"
GROQ_URL = f"{GROQ.base_url}/chat/completions"
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"failure_class": {"type": "string"}},
    "required": ["failure_class"],
}


def completion_body(
    content: str = "ok", *, prompt: int = 11, completion: int = 7
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "model": "server-side-name",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }


def request(schema: dict[str, Any] | None = None) -> CompletionRequest:
    return CompletionRequest(
        model=GEMINI.name,
        messages=[Message(role=Role.USER, content="why did this build fail")],
        response_schema=schema,
    )


def spec_with(*, max_retries: int) -> ModelSpec:
    quirks = dataclasses.replace(GEMINI.quirks, max_retries=max_retries)
    return dataclasses.replace(GEMINI, quirks=quirks)


class StepClock:
    """Advances a fixed step on every read, so latency is exactly one step."""

    STEP_SECONDS = 0.25

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += self.STEP_SECONDS
        return self.now


class RecordingLimiter(RateLimiter):
    """Real limiter that also remembers what the client asked of it."""

    def __init__(self, state_dir: Path) -> None:
        quirks = ProviderQuirks(
            native_json_schema=True,
            supports_tools=True,
            rpm=None,
            rpd=100,
            tpd=None,
            context_window=1024,
            max_retries=0,
        )
        super().__init__(
            Provider.GEMINI,
            quirks,
            state_dir=state_dir,
            clock=lambda: 0.0,
            sleep=_never_sleep,
            today=lambda: date(2026, 8, 5),
        )
        self.acquires = 0
        self.recorded: list[Usage] = []

    def acquire(self) -> None:
        self.acquires += 1
        super().acquire()

    def record(self, usage: Usage) -> None:
        self.recorded.append(usage)
        super().record(usage)


def _never_sleep(seconds: float) -> None:
    raise AssertionError(f"test tried to sleep for {seconds} seconds")


@pytest.fixture
def sleeps() -> list[float]:
    return []


def client(
    spec: ModelSpec = GEMINI,
    *,
    api_key: str | None = API_KEY,
    rate_limiter: RateLimiter | None = None,
    sleeps: list[float] | None = None,
) -> OpenAICompatClient:
    return OpenAICompatClient(
        spec,
        api_key,
        rate_limiter=rate_limiter,
        clock=StepClock(),
        sleep=sleeps.append if sleeps is not None else _never_sleep,
    )


def test_happy_path_parses_content_and_usage(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(GEMINI_URL).respond(json=completion_body("root cause: flaky test"))
    with client() as model:
        result = model.complete(request())
    assert result.content == "root cause: flaky test"
    assert result.usage == Usage(input_tokens=11, output_tokens=7)
    assert result.latency_ms == pytest.approx(StepClock.STEP_SECONDS * 1000)
    assert result.model == GEMINI.name


def test_payload_uses_the_provider_model_id(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(GROQ_URL).respond(json=completion_body())
    with client(GROQ) as model:
        model.complete(request())
    sent = _sent_json(route)
    assert sent["model"] == "llama-3.3-70b-versatile"
    assert sent["messages"] == [{"role": "user", "content": "why did this build fail"}]


def test_no_schema_sends_no_response_format(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(GEMINI_URL).respond(json=completion_body())
    with client() as model:
        model.complete(request())
    assert "response_format" not in _sent_json(route)


def test_native_provider_gets_a_strict_json_schema(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(GEMINI_URL).respond(json=completion_body())
    with client() as model:
        model.complete(request(SCHEMA))
    assert _sent_json(route)["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "triage", "strict": True, "schema": SCHEMA},
    }


def test_non_native_provider_falls_back_to_json_object(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(GROQ_URL).respond(json=completion_body())
    with client(GROQ) as model:
        model.complete(request(SCHEMA))
    assert _sent_json(route)["response_format"] == {"type": "json_object"}


def test_api_key_is_sent_as_a_bearer_token(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(GEMINI_URL).respond(json=completion_body())
    with client() as model:
        model.complete(request())
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {API_KEY}"


def test_no_api_key_sends_no_authorization_header(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(GEMINI_URL).respond(json=completion_body())
    with client(api_key=None) as model:
        model.complete(request())
    assert "Authorization" not in route.calls.last.request.headers


def test_retry_after_is_honoured_then_the_retry_succeeds(
    respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = respx_mock.post(GEMINI_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}, json={"error": "slow down"}),
            httpx.Response(200, json=completion_body("second time lucky")),
        ]
    )
    with client(sleeps=sleeps) as model:
        result = model.complete(request())
    assert result.content == "second time lucky"
    assert route.call_count == 2
    assert sleeps == [7.0]


def test_server_errors_use_jittered_backoff(
    respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    respx_mock.post(GEMINI_URL).mock(
        side_effect=[
            httpx.Response(503, json={"error": "unavailable"}),
            httpx.Response(200, json=completion_body()),
        ]
    )
    with client(sleeps=sleeps) as model:
        model.complete(request())
    assert len(sleeps) == 1
    assert 0.5 <= sleeps[0] <= 0.625


def test_retries_are_capped_by_max_retries(
    respx_mock: respx.MockRouter, sleeps: list[float]
) -> None:
    route = respx_mock.post(GEMINI_URL).respond(500, json={"error": "boom"})
    with pytest.raises(ModelError) as excinfo, client(spec_with(max_retries=2), sleeps=sleeps) as m:
        m.complete(request())
    assert route.call_count == 3
    assert len(sleeps) == 2
    assert "500" in str(excinfo.value)


def test_non_retryable_status_fails_immediately(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(GEMINI_URL).respond(400, json={"error": "bad request"})
    with pytest.raises(ModelError) as excinfo, client() as model:
        model.complete(request())
    assert route.call_count == 1
    assert "400" in str(excinfo.value)
    assert GEMINI.base_url in str(excinfo.value)


def test_api_key_never_reaches_the_error_text(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(GEMINI_URL).respond(401, json={"error": f"API key {API_KEY} is not authorised"})
    with pytest.raises(ModelError) as excinfo, client() as model:
        model.complete(request())
    message = str(excinfo.value)
    assert API_KEY not in message
    assert repr(excinfo.value).find(API_KEY) == -1
    assert "[redacted]" in message


def test_empty_choices_raise_a_model_error(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(GEMINI_URL).respond(json={"choices": []})
    with pytest.raises(ModelError, match="no choices"), client() as model:
        model.complete(request())


def test_null_content_is_an_error_not_an_empty_answer(respx_mock: respx.MockRouter) -> None:
    """Passing an empty answer downstream hides the failure at the scoring layer."""
    body = completion_body()
    body["choices"][0]["message"]["content"] = None
    respx_mock.post(GEMINI_URL).respond(json=body)
    with client() as model, pytest.raises(ModelError, match="empty content"):
        model.complete(request())


def test_rate_limiter_is_acquired_before_and_recorded_after(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.post(GEMINI_URL).respond(json=completion_body(prompt=40, completion=9))
    limiter = RecordingLimiter(tmp_path)
    with client(rate_limiter=limiter) as model:
        model.complete(request())
    assert limiter.acquires == 1
    assert limiter.recorded == [Usage(input_tokens=40, output_tokens=9)]
    stored = limiter.usage_today()
    assert stored.requests == 1
    assert stored.input_tokens == 40
    assert stored.output_tokens == 9


def test_failed_call_still_acquires_but_records_nothing(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.post(GEMINI_URL).respond(400, json={"error": "nope"})
    limiter = RecordingLimiter(tmp_path)
    with pytest.raises(ModelError), client(rate_limiter=limiter) as model:
        model.complete(request())
    assert limiter.acquires == 1
    assert limiter.recorded == []


def _sent_json(route: respx.Route) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(route.calls.last.request.content)
    return payload
