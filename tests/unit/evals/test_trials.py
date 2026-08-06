"""Trial spread: a single run is a sample, not a measurement."""

from datetime import UTC, datetime

import pytest
from evals.scorecard import CaseResult, Scorecard
from evals.scorers.classification import ClassificationReport, ConfusionMatrix
from evals.scorers.localization import LocalizationReport
from evals.trials import MetricSpread, render_trials, spreads

from buildsleuth.models.taxonomy import FailureClass

CODE = FailureClass.CODE_CHANGE


def _card(accuracy: float, macro_f1: float, hit_at_1: float = 0.5) -> Scorecard:
    return Scorecard(
        git_sha="sha",
        model="m",
        prompt_hash="p",
        dataset_hash="d",
        subset="full",
        created_at=datetime(2026, 8, 6, tzinfo=UTC),
        classification=ClassificationReport(
            per_class={},
            macro_f1=macro_f1,
            accuracy=accuracy,
            confusion=ConfusionMatrix(),
            cost_weighted_error=0.5,
        ),
        localization=LocalizationReport(hit_at_1=hit_at_1, hit_at_5=1.0, mrr=0.5, n_evaluated=3),
        per_case=[CaseResult(case_id="c1", truth_class=CODE, predicted_class=CODE, correct=True)],
    )


def test_spread_is_the_worst_swing_between_runs() -> None:
    metric = MetricSpread(name="macro_f1", values=[0.714, 0.450, 0.600])
    assert metric.low == pytest.approx(0.450)
    assert metric.high == pytest.approx(0.714)
    assert metric.spread == pytest.approx(0.264)
    assert metric.mean == pytest.approx(0.588, abs=1e-3)


def test_single_trial_reports_no_stdev() -> None:
    assert MetricSpread(name="accuracy", values=[0.8]).stdev == 0.0


def test_spreads_cover_every_reported_metric() -> None:
    measured = {metric.name for metric in spreads([_card(0.8, 0.7), _card(0.6, 0.4)])}
    assert measured == {"accuracy", "macro_f1", "cost_weighted_error", "hit_at_1"}


def test_report_warns_when_noise_exceeds_a_tight_tolerance() -> None:
    """The real observation that prompted this: 0.26 swing against a 0.02 gate."""
    cards = [_card(0.833, 0.714), _card(0.750, 0.450)]
    rendered = render_trials("some-model", cards, spreads(cards))

    assert "macro_f1" in rendered
    assert "will fire on noise" in rendered
    assert "0.264" in rendered


def test_report_says_so_when_runs_agree_exactly() -> None:
    cards = [_card(0.8, 0.7), _card(0.8, 0.7)]
    rendered = render_trials("steady-model", cards, spreads(cards))

    assert "agreed exactly" in rendered
    assert "will fire on noise" not in rendered
