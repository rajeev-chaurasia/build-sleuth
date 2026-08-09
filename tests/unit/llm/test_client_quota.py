"""Quota exhaustion must fail fast rather than retry.

Retrying a spent daily allowance cannot succeed, and each doomed retry spends
another request against the very quota that ran out, so one exhausted case
costs four.
"""

import httpx
import pytest
import respx

from buildsleuth.llm.client import (
    MAX_RATE_LIMIT_WAITS,
    RATE_LIMIT_MAX_WAIT_SECONDS,
    OpenAICompatClient,
    _is_quota_exhausted,
    _retry_after_seconds,
)
from buildsleuth.llm.registry import get_model_spec
from buildsleuth.llm.types import (
    CompletionRequest,
    Message,
    ModelError,
    QuotaExhaustedError,
    Role,
)

SPEC = get_model_spec("gemini-3.5-flash")
URL = f"{SPEC.base_url}/chat/completions"

QUOTA_BODY = {
    "error": {
        "code": 429,
        "message": "You exceeded your current quota, please check your plan and billing details.",
    }
}
RATE_LIMIT_BODY = {"error": {"code": 429, "message": "Too many requests, please slow down."}}
OK_BODY = {
    "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
}


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


PER_MINUTE_BODY = (
    '{"error":{"code":429,"message":"You exceeded your current quota",'
    '"details":[{"@type":"type.googleapis.com/google.rpc.QuotaFailure",'
    '"violations":[{"quotaId":"GenerateRequestsPerMinutePerProjectPerModel"}]},'
    '{"@type":"type.googleapis.com/google.rpc.RetryInfo","retryDelay":"26s"}]}}'
)
PER_DAY_BODY = (
    '{"error":{"code":429,"message":"You exceeded your current quota",'
    '"details":[{"@type":"type.googleapis.com/google.rpc.QuotaFailure",'
    '"violations":[{"quotaId":"GenerateRequestsPerDayPerProjectPerModel"}]}]}}'
)


class TestPerMinuteIsNotPerDay:
    """Both carry the same 'exceeded your current quota' wording, and only the
    quota id separates them. Treating the per minute one as fatal cost 13 of
    61 cases on every full run, always the same ones."""

    def test_a_per_minute_limit_is_not_fatal(self) -> None:
        response = httpx.Response(429, text=PER_MINUTE_BODY)
        assert not _is_quota_exhausted(response)

    def test_a_per_day_limit_is_still_fatal(self) -> None:
        # Retrying this spends three more requests against an allowance that
        # is already gone.
        response = httpx.Response(429, text=PER_DAY_BODY)
        assert _is_quota_exhausted(response)

    def test_the_stated_delay_is_read_from_the_body(self) -> None:
        # Gemini puts it in a RetryInfo detail, not a Retry-After header.
        assert _retry_after_seconds(httpx.Response(429, text=PER_MINUTE_BODY)) == 26.0

    def test_a_header_still_wins_when_present(self) -> None:
        response = httpx.Response(429, headers={"retry-after": "5"}, text=PER_MINUTE_BODY)
        assert _retry_after_seconds(response) == 5.0

    def test_the_wait_is_capped(self) -> None:
        body = '{"details":[{"retryDelay":"9999s"}]}'
        delay = _retry_after_seconds(httpx.Response(429, text=body))
        assert delay == RATE_LIMIT_MAX_WAIT_SECONDS

    def test_a_minute_long_wait_is_allowed(self) -> None:
        # The ordinary backoff ceiling is 30 seconds, too low to clear a per
        # minute window, so the case would be lost rather than delayed.
        body = '{"details":[{"retryDelay":"55s"}]}'
        assert _retry_after_seconds(httpx.Response(429, text=body)) == 55.0

    def test_a_body_with_no_delay_falls_back_to_backoff(self) -> None:
        assert _retry_after_seconds(httpx.Response(429, text="slow down")) is None


class TestRateLimitBudget:
    """Three cases were lost to a rate limit that shared the general retry
    budget and ran it out, reported as a model error rather than congestion."""

    @respx.mock
    def test_waits_out_more_rate_limits_than_the_general_budget_allows(self) -> None:
        sleeps: list[float] = []
        route = respx.post(URL).mock(
            side_effect=[httpx.Response(429, text=PER_MINUTE_BODY)] * 4
            + [httpx.Response(200, json=OK_BODY)]
        )

        result = _client(sleeps).complete(_request())

        assert result.content == "{}"
        # More attempts than the general max_retries would have permitted.
        assert route.call_count == 5
        assert len(sleeps) == 4

    @respx.mock
    def test_gives_up_rather_than_waiting_forever(self) -> None:
        sleeps: list[float] = []
        respx.post(URL).respond(429, text=PER_MINUTE_BODY)

        with pytest.raises(ModelError):
            _client(sleeps).complete(_request())

        assert len(sleeps) <= MAX_RATE_LIMIT_WAITS + SPEC.quirks.max_retries

    @respx.mock
    def test_a_spent_daily_allowance_still_costs_one_request(self) -> None:
        sleeps: list[float] = []
        route = respx.post(URL).respond(429, text=PER_DAY_BODY)

        with pytest.raises(QuotaExhaustedError):
            _client(sleeps).complete(_request())

        assert route.call_count == 1
