"""The LLM classifier adapter: usage accounting and runner compatibility."""

import json

from evals.llm_classifier import LlmClassifier
from evals.runner import Classifier

from buildsleuth.llm.types import CompletionRequest, CompletionResult, Usage
from buildsleuth.models.taxonomy import FailureClass

MODEL = "gemini-3.6-flash"


class ScriptedClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def complete(self, request: CompletionRequest) -> CompletionResult:
        self.calls += 1
        content = self._responses.pop(0) if self._responses else "{}"
        return CompletionResult(
            content=content,
            usage=Usage(input_tokens=100, output_tokens=20),
            latency_ms=250.0,
        )


def _verdict(failure_class: str = "code_change", subcategory: str = "test_assertion") -> str:
    return json.dumps(
        {
            "failure_class": failure_class,
            "subcategory": subcategory,
            "related_to_diff": False,
            "confidence": 0.8,
            "evidence": [],
            "reasoning": "because",
        }
    )


def test_satisfies_the_runner_classifier_protocol() -> None:
    """Static binding so a signature change fails type checking, not just at runtime."""
    classifier: Classifier = LlmClassifier(ScriptedClient([_verdict()]), MODEL)
    assert classifier.name == MODEL


def test_returns_the_validated_verdict() -> None:
    classifier = LlmClassifier(ScriptedClient([_verdict()]), MODEL)
    verdict = classifier.classify("FAILED tests/test_x.py::test_y")

    assert verdict.failure_class == FailureClass.CODE_CHANGE
    assert verdict.subcategory == "test_assertion"


def test_usage_accumulates_across_cases() -> None:
    """Cost per triage is only meaningful if every call is counted."""
    classifier = LlmClassifier(ScriptedClient([_verdict(), _verdict()]), MODEL)
    classifier.classify("first failure")
    classifier.classify("second failure")

    assert classifier.usage.input_tokens == 200
    assert classifier.usage.output_tokens == 40
    assert classifier.latency_ms_total == 500.0


def test_repairs_are_counted() -> None:
    classifier = LlmClassifier(ScriptedClient(["not json", _verdict()]), MODEL)
    classifier.classify("boom")

    assert classifier.repairs == 1
    assert classifier.usage.input_tokens == 200  # both attempts billed


def test_prompt_hash_is_exposed_for_the_scorecard() -> None:
    classifier = LlmClassifier(ScriptedClient([_verdict()]), MODEL)
    assert len(classifier.prompt_hash) == 12
