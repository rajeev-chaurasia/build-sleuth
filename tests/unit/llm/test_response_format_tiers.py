"""JSON support has three tiers, and sending the wrong one breaks a model.

Some models enforce a schema, some only promise valid JSON, and some reject
response_format outright. The third group is why this is not a boolean.
"""

import json
from dataclasses import replace
from typing import Any

import respx

from buildsleuth.llm.client import OpenAICompatClient
from buildsleuth.llm.registry import get_model_spec
from buildsleuth.llm.structured import SCHEMA_INSTRUCTION, complete_structured
from buildsleuth.llm.types import CompletionRequest, CompletionResult, Message, Role, Usage
from buildsleuth.models.triage import TriageVerdict

SCHEMA: dict[str, Any] = {"type": "object", "properties": {"a": {"type": "string"}}}


def _payload() -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


def _sent_body(spec_name: str, *, supports_response_format: bool = True) -> dict[str, Any]:
    spec = get_model_spec(spec_name)
    if not supports_response_format:
        spec = replace(spec, quirks=replace(spec.quirks, supports_response_format=False))

    url = f"{spec.base_url}/chat/completions"
    with respx.mock:
        route = respx.post(url).respond(200, json=_payload())
        client = OpenAICompatClient(spec=spec, api_key="k", sleep=lambda _s: None)
        client.complete(
            CompletionRequest(
                model=spec.api_model,
                messages=[Message(role=Role.USER, content="hi")],
                response_schema=SCHEMA,
            )
        )
        body: dict[str, Any] = json.loads(route.calls[0].request.content)
        return body


def test_strict_schema_model_gets_a_json_schema_block() -> None:
    body = _sent_body("gemini-3.5-flash")
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA


def test_json_only_model_gets_plain_json_mode() -> None:
    """Groq's Llama gates strict schemas to other models, so it takes json_object."""
    body = _sent_body("groq-llama-3.3-70b")
    assert body["response_format"] == {"type": "json_object"}


def test_model_without_response_format_support_is_sent_none() -> None:
    """Sending the parameter to a model that rejects it fails the whole call."""
    body = _sent_body("openrouter-nemotron-550b")
    assert "response_format" not in body


def test_quirk_override_removes_the_parameter() -> None:
    body = _sent_body("gemini-3.5-flash", supports_response_format=False)
    assert "response_format" not in body


class RecordingClient:
    def __init__(self) -> None:
        self.requests: list[CompletionRequest] = []

    def complete(self, request: CompletionRequest) -> CompletionResult:
        self.requests.append(request)
        return CompletionResult(
            content=json.dumps(
                {
                    "failure_class": "code_change",
                    "subcategory": "test_assertion",
                    "related_to_diff": True,
                    "confidence": 0.5,
                }
            ),
            usage=Usage(input_tokens=1, output_tokens=1),
        )


def test_schema_always_travels_in_the_prompt_too() -> None:
    """The prompt copy is what makes a model with no response_format usable."""
    client = RecordingClient()
    complete_structured(client, "m", [Message(role=Role.USER, content="classify")], TriageVerdict)

    sent = client.requests[0].messages
    assert sent[0].content == "classify"
    assert SCHEMA_INSTRUCTION.split("{schema}")[0].strip() in sent[-1].content
    assert "failure_class" in sent[-1].content
