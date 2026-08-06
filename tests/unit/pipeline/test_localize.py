"""Localize stage: the sourceless-class guard, prompt wiring, and rank parsing."""

import json

import pytest

from buildsleuth.llm.types import CompletionRequest, CompletionResult, Role, Usage
from buildsleuth.models.taxonomy import FailureClass
from buildsleuth.models.triage import MAX_RANKED_FILES, FileCandidate, Localization, TriageVerdict
from buildsleuth.pipeline.localize import (
    NO_DIFF_FILES,
    build_messages,
    has_locatable_culprit,
    localize,
    ranked_paths,
)
from buildsleuth.prompts.loader import load_prompt
from buildsleuth.tools.evidence import Evidence

MODEL = "test-model"
FAILING_LOG = "FAILED tests/test_app.py::test_new_call - AssertionError"
DIFF = (
    "diff --git a/src/app.py b/src/app.py\n"
    "@@ -1 +1 @@\n"
    "-old_call()\n"
    "+new_call()\n"
    "diff --git a/tests/test_app.py b/tests/test_app.py\n"
    "@@ -1 +1 @@\n"
    "-assert old_call()\n"
    "+assert new_call()\n"
)


class ScriptedClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.requests: list[CompletionRequest] = []

    def complete(self, request: CompletionRequest) -> CompletionResult:
        self.requests.append(request)
        content = self._responses.pop(0) if self._responses else "{}"
        return CompletionResult(content=content, usage=Usage(input_tokens=1, output_tokens=1))


def _verdict(failure_class: FailureClass, subcategory: str = "test_assertion") -> TriageVerdict:
    return TriageVerdict(
        failure_class=failure_class,
        subcategory=subcategory,
        related_to_diff=True,
        confidence=0.9,
        reasoning="an assertion in the changed module failed",
    )


def _localization_json(*paths: str) -> str:
    return json.dumps(
        {
            "ranked_files": [
                {"path": path, "confidence": 0.8, "why": "named in the traceback"} for path in paths
            ]
        }
    )


def _evidence(log_text: str = FAILING_LOG, diff_text: str | None = DIFF) -> Evidence:
    return Evidence(log_text=log_text, diff_text=diff_text)


def _paths(count: int) -> list[str]:
    return [f"src/file_{index}.py" for index in range(count)]


@pytest.mark.parametrize(
    "failure_class",
    [FailureClass.CODE_CHANGE, FailureClass.PIPELINE_CONFIG],
)
def test_source_backed_classes_have_a_locatable_culprit(failure_class: FailureClass) -> None:
    assert has_locatable_culprit(_verdict(failure_class)) is True


@pytest.mark.parametrize(
    "failure_class",
    [FailureClass.FLAKY_TEST, FailureClass.INFRA_ENVIRONMENT],
)
def test_sourceless_classes_have_no_locatable_culprit(failure_class: FailureClass) -> None:
    assert has_locatable_culprit(_verdict(failure_class)) is False


@pytest.mark.parametrize(
    "failure_class",
    [FailureClass.FLAKY_TEST, FailureClass.INFRA_ENVIRONMENT],
)
def test_sourceless_classes_never_reach_the_model(failure_class: FailureClass) -> None:
    """The guard that stops the agent inventing a file for a failure the repo did not cause."""
    client = ScriptedClient([_localization_json("src/app.py")])

    assert localize(client, MODEL, _verdict(failure_class), _evidence()) is None
    assert client.requests == []


def test_localize_returns_ranked_files_for_a_code_change() -> None:
    client = ScriptedClient([_localization_json("src/app.py", "tests/test_app.py")])
    result = localize(client, MODEL, _verdict(FailureClass.CODE_CHANGE), _evidence())

    assert result is not None
    assert ranked_paths(result.value) == ["src/app.py", "tests/test_app.py"]
    assert result.value.ranked_files[0].confidence == 0.8
    assert result.repaired is False
    assert len(client.requests) == 1


def test_an_overlong_ranking_is_trimmed_without_a_second_call() -> None:
    """The cap is presentation, so arguing about a sixth entry is not worth a repair turn."""
    oversized = _localization_json(*_paths(MAX_RANKED_FILES + 2))
    client = ScriptedClient([oversized])
    result = localize(client, MODEL, _verdict(FailureClass.CODE_CHANGE), _evidence())

    assert result is not None
    assert len(result.value.ranked_files) == MAX_RANKED_FILES
    assert result.repaired is False
    assert len(client.requests) == 1


def test_trimming_keeps_the_highest_ranked_files() -> None:
    """Order carries the model's confidence, so the tail is what to drop."""
    paths = _paths(MAX_RANKED_FILES + 2)
    client = ScriptedClient([_localization_json(*paths)])
    result = localize(client, MODEL, _verdict(FailureClass.CODE_CHANGE), _evidence())

    assert result is not None
    assert [candidate.path for candidate in result.value.ranked_files] == paths[:MAX_RANKED_FILES]


def test_an_empty_ranking_is_a_legitimate_answer() -> None:
    """No culprit file is often correct, so it must not be treated as a parse failure."""
    client = ScriptedClient([_localization_json()])
    result = localize(client, MODEL, _verdict(FailureClass.CODE_CHANGE), _evidence())

    assert result is not None
    assert result.value.ranked_files == []
    assert result.repaired is False
    assert len(client.requests) == 1


def test_messages_carry_the_verdict_and_run_context() -> None:
    body = build_messages(
        _verdict(FailureClass.PIPELINE_CONFIG, "yaml_syntax"),
        _evidence(),
        load_prompt("localize"),
        repo="octo/demo",
        failed_job="build",
        failed_step="pytest",
    )[0].content

    assert "pipeline_config" in body
    assert "yaml_syntax" in body
    assert "octo/demo" in body
    assert "build" in body
    assert "pytest" in body
    assert "an assertion in the changed module failed" in body


def test_messages_include_the_condensed_log_and_diff_paths() -> None:
    messages = build_messages(
        _verdict(FailureClass.CODE_CHANGE), _evidence(), load_prompt("localize")
    )
    body = messages[0].content

    assert FAILING_LOG in body
    assert "src/app.py" in body
    assert "tests/test_app.py" in body


def test_messages_without_a_diff_say_so_rather_than_leaving_a_blank_block() -> None:
    body = build_messages(
        _verdict(FailureClass.CODE_CHANGE),
        _evidence(diff_text=None),
        load_prompt("localize"),
    )[0].content

    assert NO_DIFF_FILES in body


def test_build_messages_produces_a_single_user_turn() -> None:
    messages = build_messages(
        _verdict(FailureClass.CODE_CHANGE), _evidence(), load_prompt("localize")
    )
    assert len(messages) == 1
    assert messages[0].role is Role.USER


def test_ranked_paths_preserves_order() -> None:
    localization = Localization(
        ranked_files=[
            FileCandidate(path="src/app.py", confidence=0.9),
            FileCandidate(path="tests/test_app.py", confidence=0.4),
        ]
    )
    assert ranked_paths(localization) == ["src/app.py", "tests/test_app.py"]


def test_ranked_paths_of_none_is_empty() -> None:
    assert ranked_paths(None) == []
