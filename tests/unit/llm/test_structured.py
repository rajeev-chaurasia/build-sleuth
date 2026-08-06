"""Structured output enforcement and the single repair retry.

Uses a scripted fake client rather than a mocked HTTP layer: the behaviour
under test is the retry protocol, not transport.
"""

import pytest
from pydantic import BaseModel, Field

from buildsleuth.llm.structured import (
    REPAIR_ATTEMPTS,
    StructuredOutputError,
    complete_structured,
    extract_json,
)
from buildsleuth.llm.types import CompletionRequest, CompletionResult, Message, Role, Usage

MODEL = "test-model"


class Answer(BaseModel):
    label: str
    score: float = Field(ge=0.0, le=1.0)


class ScriptedClient:
    """Returns canned responses in order and records what it was asked."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.requests: list[CompletionRequest] = []

    def complete(self, request: CompletionRequest) -> CompletionResult:
        self.requests.append(request)
        content = self._responses.pop(0) if self._responses else "{}"
        return CompletionResult(
            content=content,
            usage=Usage(input_tokens=10, output_tokens=5),
            latency_ms=12.5,
            model=MODEL,
        )


def _messages() -> list[Message]:
    return [Message(role=Role.USER, content="classify this")]


def test_valid_first_answer_needs_no_repair() -> None:
    client = ScriptedClient(['{"label": "flake", "score": 0.8}'])
    result = complete_structured(client, MODEL, _messages(), Answer)

    assert result.value == Answer(label="flake", score=0.8)
    assert result.repaired is False
    assert len(client.requests) == 1
    assert result.usage.input_tokens == 10


def test_schema_is_sent_to_the_provider() -> None:
    client = ScriptedClient(['{"label": "a", "score": 0.1}'])
    complete_structured(client, MODEL, _messages(), Answer)

    schema = client.requests[0].response_schema
    assert schema is not None
    assert "label" in schema["properties"]


def test_invalid_answer_is_repaired_once() -> None:
    client = ScriptedClient(
        ['{"label": "flake", "score": 9.0}', '{"label": "flake", "score": 0.9}']
    )
    result = complete_structured(client, MODEL, _messages(), Answer)

    assert result.value.score == 0.9
    assert result.repaired is True
    assert len(client.requests) == 2
    assert result.usage.input_tokens == 20  # both calls counted
    assert result.latency_ms == pytest.approx(25.0)


def test_repair_prompt_shows_the_model_its_own_errors() -> None:
    client = ScriptedClient(["not json at all", '{"label": "a", "score": 0.1}'])
    complete_structured(client, MODEL, _messages(), Answer)

    repair_messages = client.requests[1].messages
    assert repair_messages[-2].role == Role.ASSISTANT
    assert repair_messages[-2].content == "not json at all"
    assert "did not match the required schema" in repair_messages[-1].content
    assert "not json at all" in repair_messages[-1].content


def test_two_failures_raise_rather_than_loop_forever() -> None:
    client = ScriptedClient(["nope", "still nope"])
    with pytest.raises(StructuredOutputError, match="Answer did not validate"):
        complete_structured(client, MODEL, _messages(), Answer)

    assert len(client.requests) == REPAIR_ATTEMPTS + 1


def test_original_messages_are_not_mutated() -> None:
    """A caller reusing its message list must not inherit repair turns."""
    client = ScriptedClient(["bad", '{"label": "a", "score": 0.1}'])
    messages = _messages()
    complete_structured(client, MODEL, messages, Answer)

    assert len(messages) == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a": 1}', '{"a": 1}'),
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('```\n{"a": 1}\n```', '{"a": 1}'),
        ('Here you go:\n{"a": 1}', '{"a": 1}'),
        ('  {"a": 1}  ', '{"a": 1}'),
    ],
)
def test_extract_json_handles_chatty_and_fenced_answers(raw: str, expected: str) -> None:
    assert extract_json(raw) == expected


def test_extract_json_keeps_nested_objects_intact() -> None:
    raw = 'Result:\n{"outer": {"inner": [1, 2]}}\nthanks'
    assert extract_json(raw) == '{"outer": {"inner": [1, 2]}}'
