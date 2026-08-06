"""Run a classifier over the dataset and emit a scorecard.

Deliberately free of LLM specifics: anything exposing `classify(log, diff)`
can be evaluated, which is what lets the regex baseline and a model be scored
by exactly the same code path.
"""

import subprocess
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from buildsleuth.dataset.loader import case_dir_for, load_cases, read_case_diff, read_case_log
from buildsleuth.dataset.manifest import dataset_hash
from buildsleuth.models.case import TriageCase
from buildsleuth.models.taxonomy import FailureClass
from buildsleuth.models.triage import TriageVerdict
from evals.scorecard import CaseResult, CostSummary, Scorecard, build_scorecard

FULL_SUBSET = "full"
UNKNOWN_GIT_SHA = "unknown"
NO_PROMPT_HASH = "none"
MILLISECONDS_PER_SECOND = 1000.0


class Classifier(Protocol):
    name: str

    def classify(self, log_text: str, diff_text: str | None = None) -> TriageVerdict: ...


def git_sha(repo_root: Path) -> str:
    """Short commit sha of the working tree, or a placeholder outside a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return UNKNOWN_GIT_SHA
    return result.stdout.strip() or UNKNOWN_GIT_SHA


def evaluate(
    classifier: Classifier,
    dataset_dir: Path,
    subset: str | None = None,
    prompt_hash: str = NO_PROMPT_HASH,
    repo_root: Path | None = None,
) -> Scorecard:
    """Score a classifier over every case in the subset."""
    cases = load_cases(dataset_dir, subset=subset)
    results: list[CaseResult] = []
    schema_failures = 0
    started = time.perf_counter()

    for case in cases:
        result, failed = _score_case(classifier, dataset_dir, case)
        results.append(result)
        schema_failures += int(failed)

    wall_seconds = time.perf_counter() - started
    cost = CostSummary(
        total_input_tokens=0,
        total_output_tokens=0,
        total_requests=len(cases),
        wall_seconds=wall_seconds,
        schema_failure_rate=schema_failures / len(cases) if cases else 0.0,
    )
    return build_scorecard(
        git_sha=git_sha(repo_root or dataset_dir.parent),
        model=classifier.name,
        prompt_hash=prompt_hash,
        dataset_hash=dataset_hash(cases, dataset_dir),
        subset=subset or FULL_SUBSET,
        created_at=datetime.now(UTC),
        per_case=results,
        cost=cost,
    )


def _score_case(
    classifier: Classifier, dataset_dir: Path, case: TriageCase
) -> tuple[CaseResult, bool]:
    """Score one case. A classifier error is recorded, never raised."""
    case_dir = case_dir_for(dataset_dir, case)
    truth = case.ground_truth
    started = time.perf_counter()
    try:
        log_text = read_case_log(case_dir, case)
        diff_text = read_case_diff(case_dir, case)
        verdict = classifier.classify(log_text, diff_text)
    except Exception as error:
        return (
            CaseResult(
                case_id=case.case_id,
                truth_class=truth.failure_class,
                predicted_class=None,
                correct=False,
                subcategory_correct=False,
                related_to_diff_correct=None,
                localization_rank=None,
                latency_ms=None,
                error=f"{type(error).__name__}: {error}",
            ),
            True,
        )

    latency_ms = (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
    result = CaseResult(
        case_id=case.case_id,
        truth_class=truth.failure_class,
        predicted_class=verdict.failure_class,
        correct=verdict.failure_class == truth.failure_class,
        subcategory_correct=verdict.subcategory == truth.subcategory,
        # None means the case cannot settle the question, so it is not scored.
        related_to_diff_correct=(
            None
            if truth.related_to_diff is None
            else verdict.related_to_diff == truth.related_to_diff
        ),
        localization_rank=None,
        latency_ms=latency_ms,
        error=None,
    )
    return result, False


def truth_labels(cases: Sequence[TriageCase]) -> list[FailureClass]:
    """Ground truth classes, for baselines that need to know the label distribution."""
    return [case.ground_truth.failure_class for case in cases]
