"""Regression tests for gate defects found in QA review.

The headline defect: a model that stopped answering most cases scored better
on every metric than one that answered them all, and the gate passed it.
"""

from datetime import UTC, datetime

import pytest
from evals.diff_baseline import Tolerances, compare
from evals.scorecard import CaseResult, Scorecard, build_scorecard, render_markdown

from buildsleuth.models.taxonomy import FailureClass

CODE = FailureClass.CODE_CHANGE
FLAKE = FailureClass.FLAKY_TEST


def _answered(case_id: str, truth: FailureClass, predicted: FailureClass) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        truth_class=truth,
        predicted_class=predicted,
        correct=truth == predicted,
    )


def _crashed(case_id: str, truth: FailureClass) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        truth_class=truth,
        predicted_class=None,
        correct=False,
        error="RuntimeError: model exploded",
    )


def _card(
    results: list[CaseResult],
    subset: str = "smoke",
    dataset: str = "d1",
    model: str = "test-model",
    prompt_hash: str = "p1",
) -> Scorecard:
    return build_scorecard(
        git_sha="abc1234",
        model=model,
        prompt_hash=prompt_hash,
        dataset_hash=dataset,
        subset=subset,
        per_case=results,
        created_at=datetime(2026, 8, 6, tzinfo=UTC),
    )


def test_dropping_cases_cannot_pass_the_gate() -> None:
    """Answering fewer cases raises accuracy, so coverage must be gated too."""
    baseline = _card(
        [_answered(f"c{i}", CODE, CODE if i < 6 else FLAKE) for i in range(10)],
    )
    # Answers only the four it gets right, crashes on the rest.
    current = _card(
        [_answered(f"c{i}", CODE, CODE) for i in range(4)]
        + [_crashed(f"c{i}", CODE if i < 6 else FLAKE) for i in range(4, 10)],
    )

    assert current.classification is not None
    assert baseline.classification is not None
    assert current.classification.accuracy > baseline.classification.accuracy

    report = compare(baseline, current, Tolerances())
    assert report.regressed, "a model that stopped answering must not pass the gate"
    coverage = next(delta for delta in report.deltas if delta.name == "coverage")
    assert coverage.regressed
    assert coverage.current == pytest.approx(0.4)


def test_total_collapse_is_a_regression_not_a_skip() -> None:
    """Every case erroring drops the report entirely; that must fail, not pass."""
    baseline = _card([_answered("c1", CODE, CODE), _answered("c2", FLAKE, FLAKE)])
    current = _card([_crashed("c1", CODE), _crashed("c2", FLAKE)])

    assert current.classification is None
    assert compare(baseline, current, Tolerances()).regressed


def test_comparing_different_subsets_is_refused() -> None:
    baseline = _card([_answered("c1", CODE, CODE)], subset="smoke")
    current = _card([_answered("c1", CODE, CODE)], subset="full")

    report = compare(baseline, current, Tolerances())
    assert report.regressed
    assert any("subset changed" in reason for reason in report.incomparable_reasons)


def test_comparing_different_datasets_is_refused() -> None:
    baseline = _card([_answered("c1", CODE, CODE)], dataset="d1")
    current = _card([_answered("c1", CODE, CODE)], dataset="d2")

    report = compare(baseline, current, Tolerances())
    assert report.regressed
    assert any("dataset changed" in reason for reason in report.incomparable_reasons)


def test_comparing_different_models_is_refused() -> None:
    """Two models share a subset and a dataset, so nothing else caught this.

    The keyless pull request gate scored the regex baseline and compared it
    against a scorecard from a model, and the gap between the two models was
    reported as a regression in the revision under test.
    """
    baseline = _card([_answered("c1", CODE, CODE)], model="gemini-3.1-flash-lite")
    current = _card([_answered("c1", CODE, CODE)], model="baseline-regex")

    report = compare(baseline, current, Tolerances())
    assert report.regressed
    assert any("model changed" in reason for reason in report.incomparable_reasons)


def test_comparing_across_prompt_hashes_is_allowed() -> None:
    """Showing what a prompt edit did to the numbers is what the gate is for."""
    baseline = _card([_answered("c1", CODE, CODE)], prompt_hash="p1")
    current = _card([_answered("c1", CODE, CODE)], prompt_hash="p2")

    report = compare(baseline, current, Tolerances())
    assert report.incomparable_reasons == []
    assert not report.regressed


def test_identical_scorecards_still_pass() -> None:
    """The gate must not cry wolf on an unchanged run."""
    results = [_answered("c1", CODE, CODE), _answered("c2", FLAKE, CODE)]
    assert not compare(_card(results), _card(results), Tolerances()).regressed


def test_scorecard_shows_how_many_cases_were_evaluated() -> None:
    """Accuracy over a subset must never be mistaken for accuracy over the whole set."""
    card = _card([_answered("c1", CODE, CODE), _crashed("c2", FLAKE)])
    assert "evaluated cases | 1 of 2" in render_markdown(card)


def test_missing_verdict_requires_an_error() -> None:
    """Silently dropping a case would remove it from every metric unnoticed."""
    with pytest.raises(ValueError, match="no verdict and no error"):
        CaseResult(case_id="c1", truth_class=CODE, predicted_class=None)


def test_case_cannot_be_correct_without_a_verdict() -> None:
    with pytest.raises(ValueError, match="cannot be correct without a verdict"):
        CaseResult(case_id="c1", truth_class=CODE, predicted_class=None, correct=True, error="boom")


def test_macro_f1_is_capped_when_the_subset_misses_classes() -> None:
    """Perfect predictions over two classes still cap macro F1 at 0.5 by design."""
    card = _card([_answered("c1", CODE, CODE), _answered("c2", FLAKE, FLAKE)])
    assert card.classification is not None
    assert card.classification.accuracy == pytest.approx(1.0)
    assert card.classification.macro_f1 == pytest.approx(0.5)
    assert "macro f1 (all 4 classes)" in render_markdown(card)
