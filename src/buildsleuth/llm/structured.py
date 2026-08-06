"""Force a model response into a pydantic model.

Every model output in this project is a proposal that must validate before it
is used. When validation fails we retry exactly once, showing the model its
own errors. A second failure is recorded as a miss rather than raised, so one
stubborn case never aborts an eval run.
"""

import json
import re

from pydantic import BaseModel, ValidationError

from buildsleuth.llm.types import (
    CompletionRequest,
    CompletionResult,
    Message,
    ModelClient,
    Role,
    Usage,
)

REPAIR_ATTEMPTS = 1
# Providers enforce schemas to three different degrees and some not at all, so
# the schema always travels in the prompt too. It costs a few tokens and means
# one code path works everywhere.
SCHEMA_INSTRUCTION = (
    "Reply with a single JSON object matching this schema exactly."
    " No prose, no code fences, no extra keys.\n{schema}"
)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(?P<body>.*?)```", re.DOTALL)
_FIRST_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class StructuredOutputError(Exception):
    """The model never produced output matching the schema."""


class StructuredResult[ModelT: BaseModel](BaseModel):
    """A validated value plus what it cost to get it."""

    value: ModelT
    usage: Usage
    latency_ms: float
    repaired: bool
    raw_responses: list[str]


def extract_json(text: str) -> str:
    """Pull the JSON object out of a response that may be fenced or chatty."""
    fenced = _JSON_FENCE_RE.search(text)
    candidate = fenced.group("body") if fenced else text
    stripped = candidate.strip()
    if stripped.startswith("{"):
        return stripped
    match = _FIRST_OBJECT_RE.search(stripped)
    return match.group(0) if match else stripped


def _validation_feedback(error: Exception, raw: str) -> str:
    """Show the model exactly what was wrong, so the retry is informed."""
    return (
        "Your previous answer did not match the required schema.\n"
        f"Answer was:\n{raw}\n\n"
        f"Errors:\n{error}\n\n"
        "Reply with corrected JSON only. No prose, no code fences."
    )


def _parse[T: BaseModel](text: str, output_model: type[T]) -> T:
    payload = extract_json(text)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"response was not JSON: {error}") from error
    return output_model.model_validate(data)


def complete_structured[T: BaseModel](
    client: ModelClient,
    model: str,
    messages: list[Message],
    output_model: type[T],
    temperature: float = 0.0,
    max_tokens: int = 2048,
) -> StructuredResult[T]:
    """Call the model and validate its answer, repairing once on failure."""
    schema = output_model.model_json_schema()
    conversation = [
        *messages,
        Message(
            role=Role.USER,
            content=SCHEMA_INSTRUCTION.format(schema=json.dumps(schema, separators=(",", ":"))),
        ),
    ]
    usage = Usage()
    latency_ms = 0.0
    raw_responses: list[str] = []
    last_error: Exception | None = None

    for attempt in range(REPAIR_ATTEMPTS + 1):
        result = client.complete(
            CompletionRequest(
                model=model,
                messages=conversation,
                response_schema=schema,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        )
        usage = usage + result.usage
        latency_ms += result.latency_ms
        raw_responses.append(result.content)

        try:
            value = _parse(result.content, output_model)
        except (ValidationError, ValueError) as error:
            last_error = error
            conversation = _repair_conversation(conversation, result, error)
            continue

        return StructuredResult(
            value=value,
            usage=usage,
            latency_ms=latency_ms,
            repaired=attempt > 0,
            raw_responses=raw_responses,
        )

    attempts = REPAIR_ATTEMPTS + 1
    raise StructuredOutputError(
        f"{output_model.__name__} did not validate after {attempts} attempts: {last_error}"
    )


def _repair_conversation(
    conversation: list[Message], result: CompletionResult, error: Exception
) -> list[Message]:
    return [
        *conversation,
        Message(role=Role.ASSISTANT, content=result.content),
        Message(role=Role.USER, content=_validation_feedback(error, result.content)),
    ]
