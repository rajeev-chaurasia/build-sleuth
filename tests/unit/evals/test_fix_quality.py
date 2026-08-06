"""Fix scoring: a funnel, and skipped cases counted apart from failures."""

import pytest
from evals.scorers.fix_quality import FixAttempt, fix_metrics, render_funnel

from buildsleuth.pipeline.verify import VerificationLevel


def _attempt(case_id: str, level: VerificationLevel) -> FixAttempt:
    return FixAttempt(case_id=case_id, attempted=True, level=level)


def _skipped(case_id: str, reason: str = "flaky failures have nothing to patch") -> FixAttempt:
    return FixAttempt(case_id=case_id, attempted=False, skip_reason=reason)


def test_funnel_counts_each_level_cumulatively() -> None:
    """A patch that fixed the failure also applied, so it counts at both levels."""
    report = fix_metrics(
        [
            _attempt("a", VerificationLevel.NOTHING),
            _attempt("b", VerificationLevel.APPLIES),
            _attempt("c", VerificationLevel.LINTS),
            _attempt("d", VerificationLevel.FAILING_TEST_PASSES),
            _attempt("e", VerificationLevel.NOTHING_ELSE_BROKE),
        ]
    )

    assert report.n_attempted == 5
    assert report.applies == 4
    assert report.lints == 3
    assert report.failing_test_passes == 2
    assert report.nothing_else_broke == 1


def test_skipped_cases_are_not_counted_as_failures() -> None:
    """Declining to patch a flake is correct, and scoring it as a miss rewards guessing."""
    report = fix_metrics([_attempt("a", VerificationLevel.NOTHING_ELSE_BROKE), _skipped("b")])

    assert report.n_cases == 2
    assert report.n_attempted == 1
    assert report.n_skipped == 1
    assert report.clean_fix_rate == pytest.approx(1.0)


def test_rates_are_over_attempted_not_over_all_cases() -> None:
    report = fix_metrics(
        [
            _attempt("a", VerificationLevel.FAILING_TEST_PASSES),
            _attempt("b", VerificationLevel.NOTHING),
            _skipped("c"),
            _skipped("d"),
        ]
    )

    assert report.fix_rate == pytest.approx(0.5)
    assert report.apply_rate == pytest.approx(0.5)


def test_a_patch_that_applies_but_fixes_nothing_is_visible() -> None:
    """The distinction the funnel exists for: applying is not fixing."""
    report = fix_metrics([_attempt("a", VerificationLevel.LINTS)])

    assert report.apply_rate == pytest.approx(1.0)
    assert report.fix_rate == pytest.approx(0.0)


def test_no_attempts_yields_zero_rates_rather_than_dividing_by_zero() -> None:
    report = fix_metrics([_skipped("a"), _skipped("b")])

    assert report.n_attempted == 0
    assert report.apply_rate == 0.0
    assert report.fix_rate == 0.0
    assert report.clean_fix_rate == 0.0


def test_empty_input_is_safe() -> None:
    report = fix_metrics([])
    assert report.n_cases == 0
    assert report.fix_rate == 0.0


def test_funnel_renders_every_stage_with_its_share() -> None:
    report = fix_metrics(
        [
            _attempt("a", VerificationLevel.NOTHING_ELSE_BROKE),
            _attempt("b", VerificationLevel.APPLIES),
            _skipped("c"),
        ]
    )
    rendered = render_funnel(report)

    assert "patch attempted | 2" in rendered
    assert "applies cleanly | 2" in rendered
    assert "failing test passes | 1" in rendered
    assert "not attempted, by design | 1" in rendered


def test_funnel_says_so_when_nothing_was_attempted() -> None:
    rendered = render_funnel(fix_metrics([_skipped("a"), _skipped("b")]))
    assert "No patch was attempted" in rendered
    assert "| stage |" not in rendered
