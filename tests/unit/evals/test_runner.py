"""The eval runner: the orchestration that turns a classifier into a scorecard.

Every number the project publishes comes out of `evaluate`, so the properties
pinned here are the ones a scorecard would be wrong without: dataset order,
survival of a classifier that raises, and scoring only what a case can settle.
"""

import json
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from evals.runner import (
    FULL_SUBSET,
    NO_PROMPT_HASH,
    UNKNOWN_GIT_SHA,
    Classifier,
    _rank_of_first_hit,
    evaluate,
    git_sha,
    truth_labels,
)

from buildsleuth.dataset.loader import CASE_FILE_NAME, CASES_DIR_NAME, load_cases
from buildsleuth.dataset.manifest import dataset_hash
from buildsleuth.models.taxonomy import FailureClass
from buildsleuth.models.triage import TriageVerdict

DATASET_DIR_NAME = "dataset"
LOG_NAME = "logs/failed_job.txt"
SMOKE_TAG = "smoke"

FAKE_MODEL = "fake-classifier"
PROMPT_HASH = "p1"
BOOM = "classifier exploded"
BASELINE_CONFIDENCE = 1.0

CODE_CHANGE = FailureClass.CODE_CHANGE
FLAKY_TEST = FailureClass.FLAKY_TEST
CODE_SUBCATEGORY = "test_assertion"
FLAKY_SUBCATEGORY = "async_wait"

SHORT_SHA = re.compile(r"[0-9a-f]{7,40}")
GIT_USER_ARGS = ("-c", "user.email=eval@example.com", "-c", "user.name=Eval")


def _payload(
    case_id: str,
    failure_class: FailureClass,
    subcategory: str,
    related_to_diff: bool | None,
    culprit_files: Sequence[str],
    smoke: bool,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "title": f"case {case_id}",
        "inputs": {
            "repo": "acme/widget",
            "run_id": 1,
            "head_sha": "0" * 40,
            "failed_job_name": "tests",
            "log_files": [LOG_NAME],
        },
        "ground_truth": {
            "failure_class": failure_class.value,
            "subcategory": subcategory,
            "related_to_diff": related_to_diff,
            "culprit_files": list(culprit_files),
        },
        "provenance": {
            "source": "synthetic",
            "labeling_method": "constructed",
            "verified_by_human": smoke,
            "snapshot_date": "2026-01-01",
        },
        "tags": [SMOKE_TAG] if smoke else [],
    }


def _write_case(
    dataset_dir: Path,
    case_id: str,
    *,
    failure_class: FailureClass = CODE_CHANGE,
    subcategory: str = CODE_SUBCATEGORY,
    related_to_diff: bool | None = True,
    culprit_files: Sequence[str] = (),
    smoke: bool = False,
) -> Path:
    """Write one self-contained case directory whose log carries its own id."""
    case_dir = dataset_dir / CASES_DIR_NAME / case_id
    log_path = case_dir / LOG_NAME
    log_path.parent.mkdir(parents=True)
    payload = _payload(case_id, failure_class, subcategory, related_to_diff, culprit_files, smoke)
    (case_dir / CASE_FILE_NAME).write_text(json.dumps(payload), encoding="utf-8")
    log_path.write_text(f"FAILED {case_id}\n", encoding="utf-8", newline="\n")
    return case_dir


@pytest.fixture
def dataset_dir(tmp_path: Path) -> Path:
    path = tmp_path / DATASET_DIR_NAME
    path.mkdir()
    return path


class FakeClassifier:
    """Classification only. No `localize`, so the runner must score class alone."""

    name = FAKE_MODEL

    def __init__(
        self,
        *,
        failure_class: FailureClass = CODE_CHANGE,
        subcategory: str = CODE_SUBCATEGORY,
        related_to_diff: bool = True,
        raise_on: str | None = None,
    ) -> None:
        self._failure_class = failure_class
        self._subcategory = subcategory
        self._related_to_diff = related_to_diff
        self._raise_on = raise_on
        self.seen: list[str] = []

    def classify(self, log_text: str, diff_text: str | None = None) -> TriageVerdict:
        self.seen.append(log_text)
        if self._raise_on is not None and self._raise_on in log_text:
            raise RuntimeError(BOOM)
        return TriageVerdict(
            failure_class=self._failure_class,
            subcategory=self._subcategory,
            related_to_diff=self._related_to_diff,
            confidence=BASELINE_CONFIDENCE,
        )


class FakeLocalizingClassifier(FakeClassifier):
    """Adds the optional `localize` capability the runner probes for."""

    def __init__(
        self,
        *,
        ranked: Sequence[str] = (),
        localize_raises: bool = False,
        failure_class: FailureClass = CODE_CHANGE,
        subcategory: str = CODE_SUBCATEGORY,
        related_to_diff: bool = True,
        raise_on: str | None = None,
    ) -> None:
        super().__init__(
            failure_class=failure_class,
            subcategory=subcategory,
            related_to_diff=related_to_diff,
            raise_on=raise_on,
        )
        self._ranked = list(ranked)
        self._localize_raises = localize_raises

    def localize(
        self, verdict: TriageVerdict, log_text: str, diff_text: str | None = None
    ) -> list[str]:
        if self._localize_raises:
            raise ValueError(BOOM)
        return list(self._ranked)


def _init_repo(path: Path) -> None:
    for args in (
        ["git", "init", "-q"],
        ["git", *GIT_USER_ARGS, "commit", "-q", "--allow-empty", "-m", "init"],
    ):
        subprocess.run(args, cwd=path, check=True, capture_output=True, text=True)


def test_evaluate_scores_every_case_once_in_dataset_order(dataset_dir: Path) -> None:
    for case_id in ("c3", "c1", "c2"):
        _write_case(dataset_dir, case_id)

    card = evaluate(FakeClassifier(), dataset_dir)

    assert [result.case_id for result in card.per_case] == ["c1", "c2", "c3"]
    assert card.cost.total_requests == 3


def test_evaluate_carries_the_identity_of_the_run(dataset_dir: Path) -> None:
    """A number is not quotable without the dataset and prompt that produced it."""
    _write_case(dataset_dir, "c1")
    classifier = FakeClassifier()

    card = evaluate(classifier, dataset_dir, prompt_hash=PROMPT_HASH)

    assert card.prompt_hash == PROMPT_HASH
    assert card.dataset_hash == dataset_hash(load_cases(dataset_dir), dataset_dir)
    assert card.model == classifier.name
    assert card.subset == FULL_SUBSET


def test_evaluate_scores_only_the_requested_subset(dataset_dir: Path) -> None:
    _write_case(dataset_dir, "c1", smoke=True)
    _write_case(dataset_dir, "c2")

    card = evaluate(FakeClassifier(), dataset_dir, subset=SMOKE_TAG)

    assert [result.case_id for result in card.per_case] == ["c1"]
    assert card.subset == SMOKE_TAG


def test_a_classifier_that_raises_does_not_abort_the_run(dataset_dir: Path) -> None:
    """One bad case must cost one case, never the whole scorecard."""
    for case_id in ("c1", "c2", "c3"):
        _write_case(dataset_dir, case_id)

    card = evaluate(FakeClassifier(raise_on="c2"), dataset_dir)

    failed = next(result for result in card.per_case if result.case_id == "c2")
    assert failed.predicted_class is None
    assert failed.error is not None
    assert failed.error.startswith("RuntimeError")
    assert BOOM in failed.error
    assert failed.latency_ms is None

    survivors = [result for result in card.per_case if result.case_id != "c2"]
    assert all(result.predicted_class == CODE_CHANGE for result in survivors)
    assert all(result.error is None for result in survivors)
    assert card.cost.schema_failure_rate == pytest.approx(1 / 3)


def test_a_classifier_without_localize_is_scored_on_classification_only(
    dataset_dir: Path,
) -> None:
    _write_case(dataset_dir, "c1", culprit_files=["src/app.py"])

    card = evaluate(FakeClassifier(), dataset_dir)

    assert card.classification is not None
    assert card.localization is None
    assert all(result.localization_rank is None for result in card.per_case)


def test_a_classifier_with_localize_produces_localization_metrics(dataset_dir: Path) -> None:
    _write_case(dataset_dir, "c1", culprit_files=["src/app.py"])
    classifier = FakeLocalizingClassifier(ranked=["src/other.py", "src/app.py"])

    card = evaluate(classifier, dataset_dir)

    assert card.localization is not None
    assert card.localization.n_evaluated == 1
    assert card.localization.hit_at_1 == pytest.approx(0.0)
    assert card.localization.mrr == pytest.approx(0.5)
    assert card.per_case[0].localization_rank == 2


def test_a_localization_failure_keeps_the_classification_that_succeeded(
    dataset_dir: Path,
) -> None:
    """The expensive answer is the class; a failed second call must not discard it."""
    _write_case(dataset_dir, "c1", culprit_files=["src/app.py"])

    card = evaluate(FakeLocalizingClassifier(localize_raises=True), dataset_dir)

    result = card.per_case[0]
    assert result.predicted_class == CODE_CHANGE
    assert result.correct
    assert result.error is None
    assert result.localization_rank is None
    assert card.localization is None


def test_a_case_with_no_culprit_files_contributes_no_localization_sample(
    dataset_dir: Path,
) -> None:
    """A failure with no culprit file has nothing to localize, so it is not a zero."""
    _write_case(dataset_dir, "c1", culprit_files=["src/app.py"])
    _write_case(
        dataset_dir,
        "c2",
        failure_class=FLAKY_TEST,
        subcategory=FLAKY_SUBCATEGORY,
        culprit_files=[],
    )
    classifier = FakeLocalizingClassifier(ranked=["src/app.py"])

    card = evaluate(classifier, dataset_dir)

    assert card.localization is not None
    assert card.localization.n_evaluated == 1
    assert card.localization.hit_at_1 == pytest.approx(1.0)


def test_localization_rank_is_absent_when_the_classifier_ranks_nothing(
    dataset_dir: Path,
) -> None:
    _write_case(dataset_dir, "c1", culprit_files=["src/app.py"])

    card = evaluate(FakeLocalizingClassifier(ranked=[]), dataset_dir)

    assert card.per_case[0].localization_rank is None


def test_subcategory_is_scored_against_the_ground_truth(dataset_dir: Path) -> None:
    _write_case(dataset_dir, "c1", subcategory=CODE_SUBCATEGORY)
    _write_case(dataset_dir, "c2", subcategory="compile_error")

    card = evaluate(FakeClassifier(subcategory=CODE_SUBCATEGORY), dataset_dir)

    assert card.per_case[0].subcategory_correct
    assert not card.per_case[1].subcategory_correct


def test_a_wrong_class_is_recorded_as_answered_but_incorrect(dataset_dir: Path) -> None:
    _write_case(dataset_dir, "c1", failure_class=FLAKY_TEST, subcategory=FLAKY_SUBCATEGORY)

    card = evaluate(FakeClassifier(failure_class=CODE_CHANGE), dataset_dir)

    result = card.per_case[0]
    assert result.predicted_class == CODE_CHANGE
    assert result.truth_class == FLAKY_TEST
    assert not result.correct
    assert result.error is None
    assert card.cost.schema_failure_rate == pytest.approx(0.0)


def test_related_to_diff_is_unscored_when_the_case_cannot_settle_it(
    dataset_dir: Path,
) -> None:
    """Scoring a model on a fact it was never shown measures guessing, not skill."""
    _write_case(dataset_dir, "c1", related_to_diff=True)
    _write_case(dataset_dir, "c2", related_to_diff=False)
    _write_case(dataset_dir, "c3", related_to_diff=None)

    card = evaluate(FakeClassifier(related_to_diff=True), dataset_dir)

    assert card.per_case[0].related_to_diff_correct is True
    assert card.per_case[1].related_to_diff_correct is False
    assert card.per_case[2].related_to_diff_correct is None


def test_an_empty_subset_produces_an_empty_scorecard(dataset_dir: Path) -> None:
    """No cases must give no metrics, not a division by zero."""
    _write_case(dataset_dir, "c1")

    card = evaluate(FakeClassifier(), dataset_dir, subset=SMOKE_TAG)

    assert card.per_case == []
    assert card.classification is None
    assert card.cost.schema_failure_rate == pytest.approx(0.0)


def test_evaluate_satisfies_the_classifier_protocol_statically(dataset_dir: Path) -> None:
    """Bound as a Classifier so a protocol change fails type checking, not at runtime."""
    _write_case(dataset_dir, "c1")
    classifier: Classifier = FakeClassifier()

    card = evaluate(classifier, dataset_dir, prompt_hash=NO_PROMPT_HASH)

    assert card.prompt_hash == NO_PROMPT_HASH


def test_rank_of_first_hit_is_one_based() -> None:
    assert _rank_of_first_hit(["src/app.py"], ["src/other.py", "src/app.py"]) == 2
    assert _rank_of_first_hit(["src/app.py"], ["src/app.py"]) == 1


def test_rank_of_first_hit_is_none_when_nothing_matches() -> None:
    assert _rank_of_first_hit(["src/app.py"], ["src/other.py"]) is None
    assert _rank_of_first_hit([], ["src/app.py"]) is None
    assert _rank_of_first_hit(["src/app.py"], []) is None


def test_rank_of_first_hit_compares_normalized_paths() -> None:
    """Truth comes from the dataset and ranks come from a model, in mixed shapes."""
    assert _rank_of_first_hit(["src/app.py"], ["./src/app.py"]) == 1
    assert _rank_of_first_hit(["src/app.py"], ["src\\app.py"]) == 1
    assert _rank_of_first_hit(["./src/app.py"], ["src/app.py"]) == 1


def test_rank_of_first_hit_returns_the_first_of_several_correct_paths() -> None:
    truth = ["src/app.py", "src/util.py"]
    assert _rank_of_first_hit(truth, ["src/util.py", "src/app.py"]) == 1


def test_truth_labels_follow_dataset_order(dataset_dir: Path) -> None:
    _write_case(dataset_dir, "c2", failure_class=FLAKY_TEST, subcategory=FLAKY_SUBCATEGORY)
    _write_case(dataset_dir, "c1", failure_class=CODE_CHANGE)

    assert truth_labels(load_cases(dataset_dir)) == [CODE_CHANGE, FLAKY_TEST]


def test_truth_labels_of_no_cases_is_empty() -> None:
    assert truth_labels([]) == []


def test_git_sha_outside_a_repository_returns_the_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scorecard from a source tarball must still be produced, not crash."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))

    assert git_sha(tmp_path) == UNKNOWN_GIT_SHA


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_sha_inside_a_repository_returns_a_short_sha(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    sha = git_sha(tmp_path)

    assert sha != UNKNOWN_GIT_SHA
    assert SHORT_SHA.fullmatch(sha)
