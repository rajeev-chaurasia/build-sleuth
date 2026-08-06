"""Scorecard assembly, persistence and rendering tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from evals.scorecard import (
    CaseResult,
    CostSummary,
    Scorecard,
    build_scorecard,
    load_scorecard,
    render_markdown,
    save_scorecard,
    scorecard_filename,
)

from buildsleuth.models.taxonomy import FailureClass

CODE = FailureClass.CODE_CHANGE
FLAKY = FailureClass.FLAKY_TEST
CREATED_AT = datetime(2026, 8, 5, 12, 30, 45, tzinfo=UTC)
EM_DASH = chr(0x2014)  # built from its code point so this file holds no em-dash itself

PER_CASE = [
    CaseResult(
        case_id="case-a",
        truth_class=CODE,
        predicted_class=CODE,
        correct=True,
        subcategory_correct=True,
        related_to_diff_correct=True,
        localization_rank=1,
        latency_ms=1234.5,
    ),
    CaseResult(
        case_id="case-b",
        truth_class=CODE,
        predicted_class=FLAKY,
        correct=False,
        localization_rank=None,
        latency_ms=980.0,
    ),
    CaseResult(
        case_id="case-c",
        truth_class=FLAKY,
        predicted_class=None,
        correct=False,
        error="schema validation failed",
    ),
]


def _card() -> Scorecard:
    return build_scorecard(
        git_sha="abc1234",
        model="openrouter/meta-llama:free",
        prompt_hash="p9f8e7",
        dataset_hash="d1c2b3",
        subset="smoke",
        per_case=PER_CASE,
        localization_samples=[
            (["src/a.py"], ["src/a.py"]),
            (["src/b.py"], ["x.py", "y.py"]),
            ([], []),
        ],
        cost=CostSummary(
            total_input_tokens=12_000,
            total_output_tokens=3_400,
            total_requests=3,
            wall_seconds=42.5,
            estimated_usd=0.0123,
            schema_failure_rate=1 / 3,
        ),
        created_at=CREATED_AT,
    )


def test_build_scorecard_excludes_cases_without_a_verdict() -> None:
    card = _card()
    assert card.classification is not None
    # Only case-a and case-b produced a prediction; case-c errored out.
    assert card.classification.confusion.total() == 2
    assert card.classification.accuracy == pytest.approx(0.5)  # 1 of 2
    # error_cost(CODE_CHANGE, FLAKY_TEST) is 3.0, so 3.0 / 2 = 1.5
    assert card.classification.cost_weighted_error == pytest.approx(1.5)


def test_build_scorecard_localization_excludes_empty_truth() -> None:
    card = _card()
    assert card.localization is not None
    assert card.localization.n_evaluated == 2
    assert card.localization.hit_at_1 == pytest.approx(0.5)
    assert card.localization.mrr == pytest.approx(0.5)  # (1 + 0) / 2


def test_build_scorecard_without_predictions_or_localization() -> None:
    card = build_scorecard(
        git_sha="sha",
        model="m",
        prompt_hash="p",
        dataset_hash="d",
        subset="smoke",
        per_case=[CaseResult(case_id="only", truth_class=CODE, error="boom")],
    )
    assert card.classification is None
    assert card.localization is None
    assert card.cost == CostSummary()
    assert card.schema_version == 1


def test_scorecard_filename_sanitizes_the_model_name() -> None:
    card = _card()
    assert scorecard_filename(card) == "abc1234-openrouter-meta-llama-free-p9f8e7.json"


def test_scorecard_filename_sanitizes_backslashes() -> None:
    card = _card().model_copy(update={"model": "vendor\\model:v1"})
    assert scorecard_filename(card) == "abc1234-vendor-model-v1-p9f8e7.json"


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    card = _card()
    path = save_scorecard(card, tmp_path / "results")
    assert path.name == scorecard_filename(card)
    assert path.parent.is_dir()
    assert load_scorecard(path) == card


def test_render_markdown_reports_the_headline_numbers() -> None:
    rendered = render_markdown(_card())
    assert "### BuildSleuth scorecard: `smoke`" in rendered
    assert "`openrouter/meta-llama:free`" in rendered
    assert "| accuracy | 0.500 |" in rendered
    assert "| cost weighted error (0 to 3.0) | 1.500 |" in rendered
    assert "| hit@1 | 0.500 |" in rendered
    assert "| localized cases | 2 |" in rendered
    assert "#### Per class" in rendered
    assert "#### Confusion (rows are truth)" in rendered
    assert "case-c" in rendered  # listed as a case with no verdict
    assert EM_DASH not in rendered


def test_render_markdown_without_reports() -> None:
    card = build_scorecard(
        git_sha="sha",
        model="m",
        prompt_hash="p",
        dataset_hash="d",
        subset="full",
        per_case=[],
        created_at=CREATED_AT,
    )
    rendered = render_markdown(card)
    assert "#### Per class" not in rendered
    assert "Cases with no verdict: none" in rendered
