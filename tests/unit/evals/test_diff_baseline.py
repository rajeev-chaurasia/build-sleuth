"""Regression gate tests: tolerance boundaries, flipped cases and exit codes."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from evals.diff_baseline import (
    ACCURACY,
    COST_WEIGHTED_ERROR,
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
        MACRO_F1,
        ACCURACY,
        COST_WEIGHTED_ERROR,
        HIT_AT_1,
    ]
    assert all(delta.delta == 0.0 for delta in report.deltas)


def test_macro_f1_drop_exactly_at_tolerance_is_not_flagged() -> None:
    # 0.80 - 0.78 = 0.02, the tolerance itself, so the gate stays green.
    report = compare(_card(), _card(macro_f1=0.78), TOL)
    assert _delta_named(report, MACRO_F1) is False
    assert report.regressed is False


def test_macro_f1_drop_one_epsilon_past_tolerance_is_flagged() -> None:
    # 0.80 - 0.779 = 0.021, past the 0.02 tolerance.
    report = compare(_card(), _card(macro_f1=0.779), TOL)
    assert _delta_named(report, MACRO_F1) is True
    assert report.regressed is True


def test_accuracy_tolerance_boundary() -> None:
    assert compare(_card(), _card(accuracy=0.73), TOL).regressed is False  # drop 0.02
    assert compare(_card(), _card(accuracy=0.729), TOL).regressed is True  # drop 0.021


def test_cost_weighted_error_rise_boundary() -> None:
    # Higher cost is worse, so this metric is gated in the opposite direction.
    assert compare(_card(), _card(cost_weighted_error=0.55), TOL).regressed is False  # rise 0.05
    assert compare(_card(), _card(cost_weighted_error=0.551), TOL).regressed is True  # rise 0.051


def test_cost_weighted_error_falling_is_never_a_regression() -> None:
    report = compare(_card(), _card(cost_weighted_error=0.10), TOL)
    assert _delta_named(report, COST_WEIGHTED_ERROR) is False
    assert report.regressed is False


def test_hit_at_1_tolerance_boundary() -> None:
    assert compare(_card(), _card(hit_at_1=0.58), TOL).regressed is False  # drop 0.02
    assert compare(_card(), _card(hit_at_1=0.579), TOL).regressed is True  # drop 0.021


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


def test_missing_reports_are_skipped_not_failed() -> None:
    baseline = _card()
    current = _card().model_copy(update={"classification": None, "localization": None})
    report = compare(baseline, current, TOL)
    assert report.deltas == []
    assert report.regressed is False


def test_render_comparison_markdown() -> None:
    baseline = _card(cases={"a": True, "b": False})
    current = _card(macro_f1=0.70, cases={"a": False, "b": True})
    rendered = render_comparison_markdown(compare(baseline, current, TOL))
    assert "Verdict: **REGRESSED**" in rendered
    assert "| macro_f1 | 0.800 | 0.700 | -0.100 | v | REGRESSED |" in rendered
    assert "| accuracy | 0.750 | 0.750 | +0.000 | = | ok |" in rendered
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
    current = _write(tmp_path / "current.json", _card(macro_f1=0.60))
    code = main(["--baseline", str(baseline), "--current", str(current)])
    assert code == EXIT_REGRESSED
    assert "REGRESSED" in capsys.readouterr().out
