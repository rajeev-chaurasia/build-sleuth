"""Triage stage: context assembly, taxonomy validation, and prompt wiring."""

import json

import pytest

from buildsleuth.llm.types import CompletionRequest, CompletionResult, Usage
from buildsleuth.models.taxonomy import FailureClass
from buildsleuth.models.triage import TriageVerdict
from buildsleuth.pipeline.triage import (
    MAX_DIFF_CHARS,
    NO_DIFF_PLACEHOLDER,
    InvalidVerdictError,
    TriageContext,
    build_messages,
    check_verdict,
    classify,
)
from buildsleuth.prompts.loader import load_prompt

MODEL = "test-model"


class ScriptedClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.requests: list[CompletionRequest] = []

    def complete(self, request: CompletionRequest) -> CompletionResult:
        self.requests.append(request)
        content = self._responses.pop(0) if self._responses else "{}"
        return CompletionResult(content=content, usage=Usage(input_tokens=1, output_tokens=1))


def _verdict_json(failure_class: str, subcategory: str) -> str:
    return json.dumps(
        {
            "failure_class": failure_class,
            "subcategory": subcategory,
            "related_to_diff": True,
            "confidence": 0.9,
            "evidence": ["FAILED tests/test_x.py::test_y"],
            "reasoning": "a test assertion failed",
        }
    )


def test_context_condenses_the_log() -> None:
    log = "\n".join(["routine chatter"] * 500 + ["FAILED tests/test_x.py::test_y"])
    context = TriageContext.from_log(log)

    assert "FAILED tests/test_x.py::test_y" in context.condensed_log
    assert len(context.condensed_log) < len(log)


def test_context_without_a_diff_says_so_explicitly() -> None:
    """An empty string would read to the model as an empty diff, which is different."""
    assert TriageContext.from_log("boom").diff == NO_DIFF_PLACEHOLDER


def test_oversized_diff_is_truncated_with_a_marker() -> None:
    context = TriageContext.from_log("boom", diff_text="x" * (MAX_DIFF_CHARS + 500))
    assert context.diff.endswith("...[diff truncated]")
    assert len(context.diff) < MAX_DIFF_CHARS + 100


def test_messages_carry_the_run_context() -> None:
    context = TriageContext.from_log(
        "FAILED tests/test_x.py::test_y",
        repo="octo/demo",
        workflow_name="CI",
        failed_job="build",
        failed_step="pytest",
    )
    body = build_messages(context, load_prompt("triage"))[0].content

    assert "octo/demo" in body
    assert "pytest" in body
    assert "FAILED tests/test_x.py::test_y" in body


def test_valid_verdict_passes_validation() -> None:
    verdict = TriageVerdict(
        failure_class=FailureClass.CODE_CHANGE,
        subcategory="test_assertion",
        related_to_diff=True,
        confidence=0.9,
    )
    assert check_verdict(verdict) is verdict


def test_subcategory_from_the_wrong_class_is_rejected() -> None:
    """A plausible-looking answer that mixes classes must not be scored as valid."""
    verdict = TriageVerdict(
        failure_class=FailureClass.CODE_CHANGE,
        subcategory="oom_or_disk",  # belongs to infra_environment
        related_to_diff=False,
        confidence=0.9,
    )
    with pytest.raises(InvalidVerdictError, match="not a subcategory of"):
        check_verdict(verdict)


def test_classify_returns_a_validated_verdict() -> None:
    client = ScriptedClient([_verdict_json("code_change", "test_assertion")])
    result = classify(client, MODEL, TriageContext.from_log("FAILED tests/test_x.py::test_y"))

    assert result.value.failure_class == FailureClass.CODE_CHANGE
    assert result.value.subcategory == "test_assertion"
    assert result.repaired is False


def test_classify_repairs_an_invalid_enum_then_succeeds() -> None:
    client = ScriptedClient(
        [
            _verdict_json("not_a_real_class", "test_assertion"),
            _verdict_json("flaky_test", "async_wait"),
        ]
    )
    result = classify(client, MODEL, TriageContext.from_log("timeout waiting"))

    assert result.value.failure_class == FailureClass.FLAKY_TEST
    assert result.repaired is True
    assert len(client.requests) == 2


def test_classify_raises_when_the_subcategory_is_off_taxonomy() -> None:
    client = ScriptedClient([_verdict_json("flaky_test", "compile_error")])
    with pytest.raises(InvalidVerdictError):
        classify(client, MODEL, TriageContext.from_log("boom"))
