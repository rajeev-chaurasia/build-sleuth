"""Model comparison: ranking rules and the refusal to compare unequal coverage."""

from evals.compare_models import ModelRow, failed_row, render_comparison


def _row(
    model: str,
    *,
    evaluated: int = 6,
    total: int = 6,
    accuracy: float = 0.5,
    macro_f1: float = 0.5,
    cost_weighted_error: float = 0.5,
    seconds: float = 10.0,
) -> ModelRow:
    return ModelRow(
        model=model,
        coverage=evaluated / total,
        evaluated=evaluated,
        total=total,
        accuracy=accuracy,
        macro_f1=macro_f1,
        cost_weighted_error=cost_weighted_error,
        subcategory_accuracy=0.5,
        usd_per_triage=0.001,
        seconds=seconds,
    )


def test_names_the_best_model_on_each_axis() -> None:
    rows = [
        _row("accurate", macro_f1=0.9, cost_weighted_error=0.8, seconds=100.0),
        _row("safe", macro_f1=0.4, cost_weighted_error=0.1, seconds=50.0),
        _row("quick", macro_f1=0.5, cost_weighted_error=0.5, seconds=5.0),
    ]
    rendered = render_comparison(rows)

    assert "Best macro F1: `accurate`" in rendered
    assert "Fewest expensive mistakes: `safe`" in rendered
    assert "Fastest: `quick`" in rendered


def test_warns_when_quality_and_safety_disagree() -> None:
    """Speed alone is not a reason to pick a model, and neither is one score."""
    rows = [
        _row("accurate", macro_f1=0.9, cost_weighted_error=0.9),
        _row("safe", macro_f1=0.3, cost_weighted_error=0.1),
    ]
    assert "not the one that errs most safely" in render_comparison(rows)


def test_no_such_warning_when_one_model_wins_outright() -> None:
    rows = [
        _row("best", macro_f1=0.9, cost_weighted_error=0.1),
        _row("worse", macro_f1=0.3, cost_weighted_error=0.9),
    ]
    assert "not the one that errs most safely" not in render_comparison(rows)


def test_partial_coverage_is_called_out_and_not_ranked() -> None:
    """A model scored on fewer cases must never look like a winner."""
    rows = [_row("partial", evaluated=2, total=6, macro_f1=1.0)]
    rendered = render_comparison(rows)

    assert "answered only 2 of 6" in rendered
    assert "Best macro F1" not in rendered
    assert "nothing here is rankable" in rendered


def test_complete_model_is_ranked_above_a_partial_one() -> None:
    rows = [
        _row("partial", evaluated=1, total=6, macro_f1=1.0),
        _row("complete", macro_f1=0.4),
    ]
    rendered = render_comparison(rows)

    assert "Best macro F1: `complete`" in rendered
    assert rendered.index("`complete`") < rendered.index("`partial`")


def test_unavailable_model_is_reported_rather_than_hidden() -> None:
    rows = [_row("works"), failed_row("broken", "quota is exhausted")]
    rendered = render_comparison(rows)

    assert "`broken` could not be scored: quota is exhausted" in rendered
    assert "unavailable" in rendered
