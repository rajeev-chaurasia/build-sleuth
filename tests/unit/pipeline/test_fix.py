"""Fix stage: the unpatchable-class guard, the confidence floor, and prompt wiring."""

import json

import pytest

from buildsleuth.llm.types import CompletionRequest, CompletionResult, Role, Usage
from buildsleuth.models.taxonomy import FailureClass
from buildsleuth.models.triage import FixProposal, TriageVerdict
from buildsleuth.pipeline.fix import (
    MAX_FILE_CHARS,
    MIN_CONFIDENCE,
    NO_DIFF,
    NO_FILES,
    SkipReason,
    build_messages,
    propose_fix,
    render_file_contents,
    should_attempt,
)
from buildsleuth.prompts.loader import load_prompt
from buildsleuth.tools.evidence import Evidence

MODEL = "test-model"
REPO = "octo/demo"
FAILING_LOG = "FAILED tests/test_app.py::test_new_call - AssertionError"
DIFF = "diff --git a/src/app.py b/src/app.py\n@@ -1 +1 @@\n-old_call()\n+new_call()\n"
PATCH = (
    "diff --git a/src/app.py b/src/app.py\n"
    "--- a/src/app.py\n"
    "+++ b/src/app.py\n"
    "@@ -1 +1 @@\n"
    "-old_call()\n"
    "+new_call()\n"
)
CULPRITS = ["src/app.py"]
FILE_CONTENTS = {"src/app.py": "def old_call() -> int:\n    return 1\n"}
TRUNCATION_MARKER = "...[truncated]"
# Only ever appended past the cap, so its absence proves the tail was dropped.
TAIL_SENTINEL = "TAIL_SENTINEL"

PATCHABLE = [FailureClass.CODE_CHANGE, FailureClass.PIPELINE_CONFIG]
UNPATCHABLE = [FailureClass.FLAKY_TEST, FailureClass.INFRA_ENVIRONMENT]
ALTERNATIVES = ["rerun", "pin", "quarantine"]


class ScriptedClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.requests: list[CompletionRequest] = []

    def complete(self, request: CompletionRequest) -> CompletionResult:
        self.requests.append(request)
        content = self._responses.pop(0) if self._responses else "{}"
        return CompletionResult(content=content, usage=Usage(input_tokens=1, output_tokens=1))


def _verdict(
    failure_class: FailureClass = FailureClass.CODE_CHANGE,
    subcategory: str = "test_assertion",
    confidence: float = 0.9,
    reasoning: str = "an assertion in the changed module failed",
) -> TriageVerdict:
    return TriageVerdict(
        failure_class=failure_class,
        subcategory=subcategory,
        related_to_diff=True,
        confidence=confidence,
        reasoning=reasoning,
    )


def _evidence(log_text: str = FAILING_LOG, diff_text: str | None = DIFF) -> Evidence:
    return Evidence(log_text=log_text, diff_text=diff_text)


def _proposal_json(patch: str = PATCH, strategy: str = "call the new name") -> str:
    return json.dumps(
        {
            "patch": patch,
            "strategy": strategy,
            "expected_effect": "test_new_call passes",
            "touched_files": ["src/app.py"],
        }
    )


def _body(verdict: TriageVerdict | None = None, evidence: Evidence | None = None) -> str:
    messages = build_messages(
        verdict if verdict is not None else _verdict(),
        evidence if evidence is not None else _evidence(),
        CULPRITS,
        FILE_CONTENTS,
        load_prompt("fix"),
        repo=REPO,
    )
    return messages[0].content


@pytest.mark.parametrize("failure_class", PATCHABLE)
def test_source_backed_classes_are_worth_patching(failure_class: FailureClass) -> None:
    assert should_attempt(_verdict(failure_class), CULPRITS) is None


@pytest.mark.parametrize("failure_class", UNPATCHABLE)
def test_sourceless_classes_are_skipped(failure_class: FailureClass) -> None:
    """A confident diff for a flake or a runner outage only wastes review time."""
    skip = should_attempt(_verdict(failure_class), CULPRITS)

    assert isinstance(skip, SkipReason)
    assert failure_class.value in skip.reason


@pytest.mark.parametrize("failure_class", UNPATCHABLE)
def test_a_sourceless_skip_names_what_to_do_instead(failure_class: FailureClass) -> None:
    reason = should_attempt(_verdict(failure_class), CULPRITS)

    assert reason is not None
    lowered = reason.reason.lower()
    assert all(alternative in lowered for alternative in ALTERNATIVES)


def test_confidence_below_the_floor_is_skipped() -> None:
    skip = should_attempt(_verdict(confidence=MIN_CONFIDENCE - 0.01), CULPRITS)

    assert isinstance(skip, SkipReason)
    assert "confidence" in skip.reason


def test_confidence_exactly_at_the_floor_is_attempted() -> None:
    """The floor is inclusive, so a verdict sitting on it must not be dropped."""
    assert should_attempt(_verdict(confidence=MIN_CONFIDENCE), CULPRITS) is None


def test_no_culprit_file_is_skipped() -> None:
    skip = should_attempt(_verdict(), [])

    assert isinstance(skip, SkipReason)
    assert "nothing to edit" in skip.reason


@pytest.mark.parametrize("failure_class", UNPATCHABLE)
def test_a_skipped_verdict_never_reaches_the_model(failure_class: FailureClass) -> None:
    client = ScriptedClient([_proposal_json()])
    verdict = _verdict(failure_class)

    result = propose_fix(client, MODEL, verdict, _evidence(), CULPRITS, FILE_CONTENTS)

    assert result == should_attempt(verdict, CULPRITS)
    assert client.requests == []


def test_a_low_confidence_verdict_never_reaches_the_model() -> None:
    client = ScriptedClient([_proposal_json()])

    result = propose_fix(
        client, MODEL, _verdict(confidence=0.2), _evidence(), CULPRITS, FILE_CONTENTS
    )

    assert isinstance(result, SkipReason)
    assert client.requests == []


def test_propose_fix_returns_a_parsed_patch() -> None:
    client = ScriptedClient([_proposal_json()])

    result = propose_fix(client, MODEL, _verdict(), _evidence(), CULPRITS, FILE_CONTENTS)

    assert not isinstance(result, SkipReason)
    assert result.value.patch == PATCH
    assert result.value.touched_files == ["src/app.py"]
    assert result.value.is_empty is False
    assert result.repaired is False
    assert len(client.requests) == 1


def test_an_empty_patch_is_a_valid_proposal_rather_than_an_error() -> None:
    """A model that cannot fix the cause must be able to say so without failing."""
    client = ScriptedClient([_proposal_json(patch="", strategy="cannot fix without src/dep.py")])

    result = propose_fix(client, MODEL, _verdict(), _evidence(), CULPRITS, FILE_CONTENTS)

    assert not isinstance(result, SkipReason)
    assert result.value.is_empty is True
    assert result.value.strategy == "cannot fix without src/dep.py"
    assert result.repaired is False
    assert len(client.requests) == 1


@pytest.mark.parametrize(
    ("patch", "expected"),
    [("", True), ("   \n\t  \n", True), (PATCH, False)],
)
def test_is_empty_ignores_whitespace_only_patches(patch: str, expected: bool) -> None:
    assert FixProposal(patch=patch).is_empty is expected


def test_render_file_contents_says_so_when_there_are_none() -> None:
    assert render_file_contents({}) == NO_FILES


def test_render_file_contents_labels_every_file_with_its_path() -> None:
    rendered = render_file_contents(
        {"src/app.py": "def old_call() -> int: ...", "src/util.py": "HELPER = 1"}
    )

    assert "src/app.py" in rendered
    assert "def old_call() -> int: ..." in rendered
    assert "src/util.py" in rendered
    assert "HELPER = 1" in rendered


def test_a_huge_file_is_truncated_and_marked() -> None:
    """One oversized file must not eat the whole context window silently."""
    rendered = render_file_contents({"src/big.py": "x" * MAX_FILE_CHARS + TAIL_SENTINEL})

    assert rendered.endswith(f"\n{TRUNCATION_MARKER}")
    assert TAIL_SENTINEL not in rendered


def test_a_file_exactly_at_the_cap_is_not_marked_truncated() -> None:
    rendered = render_file_contents({"src/big.py": "x" * MAX_FILE_CHARS})

    assert TRUNCATION_MARKER not in rendered


def test_messages_carry_the_verdict_and_repo() -> None:
    body = _body(verdict=_verdict(FailureClass.PIPELINE_CONFIG, "yaml_syntax"))

    assert REPO in body
    assert "pipeline_config" in body
    assert "yaml_syntax" in body
    assert "an assertion in the changed module failed" in body


def test_messages_carry_the_log_files_and_diff() -> None:
    body = _body()

    assert FAILING_LOG in body
    assert "src/app.py" in body
    assert "def old_call() -> int:" in body
    assert "+new_call()" in body


def test_messages_without_a_diff_say_so_rather_than_leaving_a_blank_block() -> None:
    assert NO_DIFF in _body(evidence=_evidence(diff_text=None))


def test_messages_without_reasoning_say_so() -> None:
    assert "not stated" in _body(verdict=_verdict(reasoning=""))


def test_build_messages_produces_a_single_user_turn() -> None:
    messages = build_messages(
        _verdict(), _evidence(), CULPRITS, FILE_CONTENTS, load_prompt("fix"), repo=REPO
    )

    assert len(messages) == 1
    assert messages[0].role is Role.USER
