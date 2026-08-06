"""Quota exhaustion must fail fast rather than retry.

Retrying a spent daily allowance cannot succeed, and each doomed retry spends
another request against the very quota that ran out, so one exhausted case
costs four.
"""

import httpx
import pytest
import respx

from buildsleuth.llm.client import OpenAICompatClient
from buildsleuth.llm.registry import get_model_spec
from buildsleuth.llm.types import CompletionRequest, Message, QuotaExhaustedError, Role

SPEC = get_model_spec("gemini-3.5-flash")
URL = f"{SPEC.base_url}/chat/completions"

QUOTA_BODY = {
    "error": {
        "code": 429,
        "message": "You exceeded your current quota, please check your plan and billing details.",
    }
}
RATE_LIMIT_BODY = {"error": {"code": 429, "message": "Too many requests, please slow down."}}


def _request() -> CompletionRequest:
    return CompletionRequest(model=SPEC.api_model, messages=[Message(role=Role.USER, content="hi")])


def _client(sleeps: list[float]) -> OpenAICompatClient:
    return OpenAICompatClient(spec=SPEC, api_key="k", sleep=sleeps.append)


@respx.mock
def test_exhausted_quota_is_not_retried() -> None:
    sleeps: list[float] = []
    route = respx.post(URL).respond(429, json=QUOTA_BODY)

    with pytest.raises(QuotaExhaustedError, match="quota is exhausted"):
        _client(sleeps).complete(_request())

    assert route.call_count == 1, "a spent quota must cost exactly one request"
    assert sleeps == []


@respx.mock
def test_plain_rate_limiting_is_still_retried() -> None:
    """The other kind of 429 does recover, so backing off is right there."""
    sleeps: list[float] = []
    route = respx.post(URL)
    route.side_effect = [
        httpx.Response(429, json=RATE_LIMIT_BODY, headers={"Retry-After": "1"}),
        httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        ),
    ]

    _client(sleeps).complete(_request())

    assert route.call_count == 2
    assert sleeps == [1.0]


@respx.mock
def test_quota_error_does_not_leak_the_api_key() -> None:
    respx.post(URL).respond(429, json=QUOTA_BODY)
    client = OpenAICompatClient(spec=SPEC, api_key="sk-secret-123", sleep=lambda _s: None)

    with pytest.raises(QuotaExhaustedError) as error:
        client.complete(_request())

    assert "sk-secret-123" not in str(error.value)
