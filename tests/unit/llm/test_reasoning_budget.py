"""Reasoning models need output budget for the thinking they do first.

Hidden reasoning tokens are billed to the same budget as the answer, so a
budget sized for the answer alone leaves a reasoning model unable to reply.
"""

import json
from typing import Any

import pytest
import respx

from buildsleuth.llm.client import OpenAICompatClient
from buildsleuth.llm.registry import (
    DIRECT_OUTPUT_TOKENS,
    REASONING_OUTPUT_TOKENS,
    get_model_spec,
    output_token_budget,
)
from buildsleuth.llm.types import CompletionRequest, Message, ModelError, Role

REASONING_MODEL = "openrouter-nemotron-550b"
DIRECT_MODEL = "gemini-3.5-flash"


def _sent_max_tokens(model: str, requested: int) -> int:
    spec = get_model_spec(model)
    with respx.mock:
        route = respx.post(f"{spec.base_url}/chat/completions").respond(
            200,
            json={
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )
        client = OpenAICompatClient(spec=spec, api_key="k", sleep=lambda _s: None)
        client.complete(
            CompletionRequest(
                model=spec.api_model,
                messages=[Message(role=Role.USER, content="hi")],
                max_tokens=requested,
            )
        )
        body: dict[str, Any] = json.loads(route.calls[0].request.content)
        return int(body["max_tokens"])


def test_reasoning_model_gets_room_to_think() -> None:
    assert _sent_max_tokens(REASONING_MODEL, DIRECT_OUTPUT_TOKENS) == REASONING_OUTPUT_TOKENS


def test_direct_model_keeps_the_smaller_budget() -> None:
    """Paying for a large budget on a model that does not think is waste."""
    assert _sent_max_tokens(DIRECT_MODEL, DIRECT_OUTPUT_TOKENS) == DIRECT_OUTPUT_TOKENS


def test_caller_asking_for_more_is_never_reduced() -> None:
    generous = REASONING_OUTPUT_TOKENS * 2
    assert _sent_max_tokens(DIRECT_MODEL, generous) == generous


def test_budget_helper_matches_the_quirk() -> None:
    assert output_token_budget(get_model_spec(REASONING_MODEL)) == REASONING_OUTPUT_TOKENS
    assert output_token_budget(get_model_spec(DIRECT_MODEL)) == DIRECT_OUTPUT_TOKENS


@respx.mock
def test_empty_answer_after_reasoning_names_the_cause() -> None:
    """The bare message for this was unreadable and cost real debugging time."""
    spec = get_model_spec(REASONING_MODEL)
    respx.post(f"{spec.base_url}/chat/completions").respond(
        200,
        json={
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
            "usage": {
                "prompt_tokens": 900,
                "completion_tokens": 2048,
                "completion_tokens_details": {"reasoning_tokens": 2048},
            },
        },
    )
    client = OpenAICompatClient(spec=spec, api_key="k", sleep=lambda _s: None)

    with pytest.raises(ModelError, match="spending 2048 tokens reasoning"):
        client.complete(
            CompletionRequest(model=spec.api_model, messages=[Message(role=Role.USER, content="x")])
        )
