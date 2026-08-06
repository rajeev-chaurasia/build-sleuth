"""Regression gate tests: tolerance boundaries, flipped cases and exit codes."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from evals.diff_baseline import (
    ACCURACY,
    COST_WEIGHTED_ERROR,
    COVERAGE,
    EXIT_OK,
    EXIT_REGRESSED,
    HIT_AT_1,
    MACRO_F1,
    ComparisonReport,
    Tolerances,
    compare,
    main,
    render_comparison_markdown,
)
from evals.scorecard import CaseResult, Scorecard
from evals.scorers.classification import ClassificationReport, ConfusionMatrix
from evals.scorers.localization import LocalizationReport

from buildsleuth.models.taxonomy import FailureClass

CODE = FailureClass.CODE_CHANGE
CREATED_AT = datetime(2026, 8, 5, tzinfo=UTC)
TOL = Tolerances()
EM_DASH = chr(0x2014)  # built from its code point so this file holds no em-dash itself

BASE_MACRO_F1 = 0.80
BASE_ACCURACY = 0.75
BASE_COST = 0.50
BASE_HIT_AT_1 = 0.60


def _card(
    *,
    macro_f1: float = BASE_MACRO_F1,
    accuracy: float = BASE_ACCURACY,
    cost_weighted_error: float = BASE_COST,
    hit_at_1: float = BASE_HIT_AT_1,
    cases: dict[str, bool] | None = None,
) -> Scorecard:
    return Scorecard(
        git_sha="sha",
        model="m",
        prompt_hash="p",
        dataset_hash="d",
        subset="smoke",
        created_at=CREATED_AT,
        classification=ClassificationReport(
            per_class={},
            macro_f1=macro_f1,
            accuracy=accuracy,
            confusion=ConfusionMatrix(),
            cost_weighted_error=cost_weighted_error,
        ),
        localization=LocalizationReport(
            hit_at_1=hit_at_1, hit_at_5=1.0, mrr=hit_at_1, n_evaluated=4
        ),
        per_case=[
            CaseResult(case_id=case_id, truth_class=CODE, predicted_class=CODE, correct=correct)
            for case_id, correct in (cases or {}).items()
        ],
    )


def _delta_named(report: ComparisonReport, name: str) -> bool:
    return next(delta.regressed for delta in report.deltas if delta.name == name)


def test_identical_scorecards_do_not_regress() -> None:
    report = compare(_card(), _card(), TOL)
    assert report.regressed is False
    assert [delta.name for delta in report.deltas] == [
        COVERAGE,
        MACRO_F1,
        ACCURACY,
        COST_WEIGHTED_ERROR,
        HIT_AT_1,
    ]
    assert all(delta.delta == 0.0 for delta in report.deltas)


# Boundaries are derived from the configured tolerances rather than written
# out, so retuning a tolerance to match measured noise does not silently stop
# testing the behaviour it guards.
EPSILON = 0.001


def test_macro_f1_drop_exactly_at_tolerance_is_not_flagged() -> None:
    at_limit = BASE_MACRO_F1 - TOL.macro_f1_drop
    report = compare(_card(), _card(macro_f1=at_limit), TOL)
    assert _delta_named(report, MACRO_F1) is False
    assert report.regressed is False


def test_macro_f1_drop_one_epsilon_past_tolerance_is_flagged() -> None:
    past_limit = BASE_MACRO_F1 - TOL.macro_f1_drop - EPSILON
    report = compare(_card(), _card(macro_f1=past_limit), TOL)
    assert _delta_named(report, MACRO_F1) is True
    assert report.regressed is True


def test_accuracy_tolerance_boundary() -> None:
    at_limit = BASE_ACCURACY - TOL.accuracy_drop
    assert compare(_card(), _card(accuracy=at_limit), TOL).regressed is False
    assert compare(_card(), _card(accuracy=at_limit - EPSILON), TOL).regressed is True


def test_cost_weighted_error_rise_boundary() -> None:
    # Higher cost is worse, so this metric is gated in the opposite direction.
    at_limit = BASE_COST + TOL.cost_weighted_error_rise
    assert compare(_card(), _card(cost_weighted_error=at_limit), TOL).regressed is False
    assert compare(_card(), _card(cost_weighted_error=at_limit + EPSILON), TOL).regressed is True


def test_cost_weighted_error_falling_is_never_a_regression() -> None:
    report = compare(_card(), _card(cost_weighted_error=0.10), TOL)
    assert _delta_named(report, COST_WEIGHTED_ERROR) is False
    assert report.regressed is False


def test_hit_at_1_tolerance_boundary() -> None:
    at_limit = BASE_HIT_AT_1 - TOL.hit_at_1_drop
    assert compare(_card(), _card(hit_at_1=at_limit), TOL).regressed is False
    assert compare(_card(), _card(hit_at_1=at_limit - EPSILON), TOL).regressed is True


def test_improvements_are_not_regressions() -> None:
    report = compare(_card(), _card(macro_f1=0.95, accuracy=0.99, hit_at_1=0.9), TOL)
    assert report.regressed is False
    assert next(d for d in report.deltas if d.name == MACRO_F1).delta == pytest.approx(0.15)


def test_newly_failing_and_passing_cases() -> None:
    baseline = _card(cases={"a": True, "b": False, "c": True, "d": True})
    current = _card(cases={"a": False, "b": True, "c": True, "e": False})
    report = compare(baseline, current, TOL)
    assert report.newly_failing_cases == ["a"]  # was correct, now wrong
    assert report.newly_passing_cases == ["b"]  # was wrong, now correct
    # "c" is unchanged, "d" is missing from current and "e" is new, so all three
    # are ignored: only cases present on both sides can flip.


def test_losing_a_report_is_a_regression() -> None:
    """Skipping a missing report would let a total collapse pass the gate."""
    baseline = _card()
    current = _card().model_copy(update={"classification": None, "localization": None})
    assert compare(baseline, current, TOL).regressed is True


def test_render_comparison_markdown() -> None:
    regressed_f1 = BASE_MACRO_F1 - TOL.macro_f1_drop - EPSILON
    baseline = _card(cases={"a": True, "b": False})
    current = _card(macro_f1=regressed_f1, cases={"a": False, "b": True})
    rendered = render_comparison_markdown(compare(baseline, current, TOL))

    assert "Verdict: **REGRESSED**" in rendered
    assert f"| macro_f1 | {BASE_MACRO_F1:.3f} | {regressed_f1:.3f} |" in rendered
    assert "| REGRESSED |" in rendered
    assert f"| accuracy | {BASE_ACCURACY:.3f} | {BASE_ACCURACY:.3f} | +0.000 | = | ok |" in rendered
    assert "Newly failing cases: `a`" in rendered
    assert "Newly passing cases: `b`" in rendered
    assert EM_DASH not in rendered


def test_render_comparison_markdown_without_flipped_cases() -> None:
    rendered = render_comparison_markdown(compare(_card(), _card(), TOL))
    assert "Verdict: **ok**" in rendered
    assert "Newly failing cases: none" in rendered
    assert "Newly passing cases: none" in rendered


def _write(path: Path, card: Scorecard) -> Path:
    path.write_text(card.model_dump_json(), encoding="utf-8")
    return path


def test_main_returns_zero_when_clean(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    baseline = _write(tmp_path / "baseline.json", _card())
    current = _write(tmp_path / "current.json", _card(macro_f1=0.78))
    code = main(["--baseline", str(baseline), "--current", str(current)])
    assert code == EXIT_OK
    assert "Verdict: **ok**" in capsys.readouterr().out


def test_main_returns_one_when_regressed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write(tmp_path / "baseline.json", _card())
    collapsed = _card(macro_f1=BASE_MACRO_F1 - TOL.macro_f1_drop - EPSILON)
    current = _write(tmp_path / "current.json", collapsed)
    code = main(["--baseline", str(baseline), "--current", str(current)])
    assert code == EXIT_REGRESSED
    assert "REGRESSED" in capsys.readouterr().out
