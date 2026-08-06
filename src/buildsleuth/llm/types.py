"""Shared model-layer types.

Kept separate from client.py so the registry, rate limiter, and structured
output helper can all import them without importing an HTTP client.
"""

from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Message(BaseModel):
    role: Role
    content: str


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class CompletionRequest(BaseModel):
    model: str
    messages: list[Message]
    response_schema: dict[str, Any] | None = None
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = 2048


class CompletionResult(BaseModel):
    content: str
    usage: Usage = Usage()
    latency_ms: float = 0.0
    model: str = ""


class ModelClient(Protocol):
    def complete(self, request: CompletionRequest) -> CompletionResult: ...


class ModelError(Exception):
    """A model call failed after the client exhausted its retries."""


class RateLimitExceededError(ModelError):
    """The configured daily or per-minute budget is spent."""


class QuotaExhaustedError(ModelError):
    """The provider says the account's allowance is gone, so retrying is futile."""
