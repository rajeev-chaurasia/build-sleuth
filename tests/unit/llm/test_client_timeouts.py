"""Timeouts must be retried, not silently dropped.

A dropped case shrinks the evaluated set, which inflates every metric computed
over what remains. This is the transport-level half of that guarantee.
"""

import httpx
import pytest
import respx

from buildsleuth.llm.client import DEFAULT_TIMEOUT_SECONDS, OpenAICompatClient
from buildsleuth.llm.registry import get_model_spec
from buildsleuth.llm.types import CompletionRequest, Message, ModelError, Role

SPEC = get_model_spec("gemini-3.6-flash")
URL = f"{SPEC.base_url}/chat/completions"


def _request() -> CompletionRequest:
    return CompletionRequest(model=SPEC.api_model, messages=[Message(role=Role.USER, content="hi")])


def _ok_payload() -> dict[str, object]:
    return {
        "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }


def _client(sleeps: list[float]) -> OpenAICompatClient:
    return OpenAICompatClient(spec=SPEC, api_key="k", sleep=sleeps.append)


@respx.mock
def test_transient_timeout_is_retried_then_succeeds() -> None:
    sleeps: list[float] = []
    route = respx.post(URL)
    route.side_effect = [
        httpx.ReadTimeout("too slow"),
        httpx.Response(200, json=_ok_payload()),
    ]

    result = _client(sleeps).complete(_request())

    assert result.content == '{"ok": true}'
    assert route.call_count == 2
    assert len(sleeps) == 1


@respx.mock
def test_persistent_timeout_raises_a_model_error() -> None:
    sleeps: list[float] = []
    respx.post(URL).mock(side_effect=httpx.ReadTimeout("still too slow"))

    with pytest.raises(ModelError, match="timed out after"):
        _client(sleeps).complete(_request())

    assert len(sleeps) == SPEC.quirks.max_retries


@respx.mock
def test_connect_timeout_is_treated_the_same() -> None:
    sleeps: list[float] = []
    route = respx.post(URL)
    route.side_effect = [
        httpx.ConnectTimeout("no route"),
        httpx.Response(200, json=_ok_payload()),
    ]

    assert _client(sleeps).complete(_request()).content == '{"ok": true}'
    assert route.call_count == 2


def test_default_timeout_allows_for_a_slow_reasoning_model() -> None:
    """Sixty seconds was too tight for large logs and dropped real cases."""
    assert DEFAULT_TIMEOUT_SECONDS >= 120.0
