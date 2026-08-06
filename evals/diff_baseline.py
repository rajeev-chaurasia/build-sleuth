"""Regression gate: compare a current scorecard against a stored baseline.

Run as `python -m evals.diff_baseline --baseline X.json --current Y.json`.
Exit code 1 means at least one metric moved past its tolerance.
"""

import argparse
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from evals.scorecard import Scorecard, load_scorecard

# Guard against float representation noise so a drift that equals the tolerance
# exactly (0.80 - 0.78 lands slightly above 0.02 in binary) is not flagged.
FLOAT_SLACK = 1e-9

MACRO_F1 = "macro_f1"
ACCURACY = "accuracy"
COST_WEIGHTED_ERROR = "cost_weighted_error"
HIT_AT_1 = "hit_at_1"
COVERAGE = "coverage"

SYMBOL_UP = "^"
SYMBOL_DOWN = "v"
SYMBOL_FLAT = "="
STATUS_REGRESSED = "REGRESSED"
STATUS_OK = "ok"
NONE_MARKER = "none"
DECIMALS = 3

EXIT_OK = 0
EXIT_REGRESSED = 1


class MetricDelta(BaseModel):
    name: str
    baseline: float
    current: float
    delta: float
    regressed: bool


class Tolerances(BaseModel):
    """How far each metric may drift before the gate fails."""

    macro_f1_drop: float = 0.02
    accuracy_drop: float = 0.02
    cost_weighted_error_rise: float = 0.05
    hit_at_1_drop: float = 0.02
    # Answering fewer cases inflates every other metric, so coverage is held
    # to a tighter bound than accuracy itself.
    coverage_drop: float = 0.0


class ComparisonReport(BaseModel):
    deltas: list[MetricDelta] = []
    regressed: bool = False
    newly_failing_cases: list[str] = []
    newly_passing_cases: list[str] = []
    incomparable_reasons: list[str] = []


def _coverage(card: Scorecard) -> float:
    """Share of cases the model actually produced a verdict for."""
    if not card.per_case:
        return 0.0
    answered = sum(1 for case in card.per_case if case.predicted_class is not None)
    return answered / len(card.per_case)


def _comparability_problems(baseline: Scorecard, current: Scorecard) -> list[str]:
    """Metrics only mean the same thing when the subset and dataset match."""
    problems: list[str] = []
    if baseline.subset != current.subset:
        problems.append(f"subset changed: {baseline.subset} to {current.subset}")
    if baseline.dataset_hash != current.dataset_hash:
        problems.append(f"dataset changed: {baseline.dataset_hash} to {current.dataset_hash}")
    return problems


def _metric_delta(
    name: str, baseline: float, current: float, tolerance: float, higher_is_better: bool
) -> MetricDelta:
    delta = current - baseline
    drift = -delta if higher_is_better else delta
    return MetricDelta(
        name=name,
        baseline=baseline,
        current=current,
        delta=delta,
        regressed=drift > tolerance + FLOAT_SLACK,
    )


def compare(baseline: Scorecard, current: Scorecard, tol: Tolerances) -> ComparisonReport:
    """Diff two scorecards.

    Coverage is compared first and always: a model that stops answering would
    otherwise raise every remaining metric and pass the gate while getting
    strictly worse. Losing a report the baseline had is likewise a regression,
    not something to skip over.
    """
    deltas: list[MetricDelta] = [
        _metric_delta(COVERAGE, _coverage(baseline), _coverage(current), tol.coverage_drop, True)
    ]
    lost_reports = (baseline.classification is not None and current.classification is None) or (
        baseline.localization is not None and current.localization is None
    )
    if baseline.classification is not None and current.classification is not None:
        before, after = baseline.classification, current.classification
        deltas += [
            _metric_delta(MACRO_F1, before.macro_f1, after.macro_f1, tol.macro_f1_drop, True),
            _metric_delta(ACCURACY, before.accuracy, after.accuracy, tol.accuracy_drop, True),
            _metric_delta(
                COST_WEIGHTED_ERROR,
                before.cost_weighted_error,
                after.cost_weighted_error,
                tol.cost_weighted_error_rise,
                False,
            ),
        ]
    if baseline.localization is not None and current.localization is not None:
        deltas.append(
            _metric_delta(
                HIT_AT_1,
                baseline.localization.hit_at_1,
                current.localization.hit_at_1,
                tol.hit_at_1_drop,
                True,
            )
        )

    was_correct = {case.case_id: case.correct for case in baseline.per_case}
    shared = [case for case in current.per_case if case.case_id in was_correct]
    newly_failing = sorted(
        case.case_id for case in shared if was_correct[case.case_id] and not case.correct
    )
    problems = _comparability_problems(baseline, current)
    return ComparisonReport(
        deltas=deltas,
        regressed=any(delta.regressed for delta in deltas) or lost_reports or bool(problems),
        newly_failing_cases=newly_failing,
        newly_passing_cases=sorted(
            case.case_id for case in shared if case.correct and not was_correct[case.case_id]
        ),
        incomparable_reasons=problems,
    )


def _direction(delta: float) -> str:
    if delta > 0.0:
        return SYMBOL_UP
    return SYMBOL_DOWN if delta < 0.0 else SYMBOL_FLAT


def _case_list(case_ids: Sequence[str]) -> str:
    return ", ".join(f"`{case_id}`" for case_id in case_ids) if case_ids else NONE_MARKER


def render_comparison_markdown(report: ComparisonReport) -> str:
    """Markdown table of metric drift plus the cases that flipped."""
    header = "### Scorecard diff versus baseline"
    verdict = STATUS_REGRESSED if report.regressed else STATUS_OK
    rows = [
        f"| {delta.name} | {delta.baseline:.{DECIMALS}f} | {delta.current:.{DECIMALS}f} "
        f"| {delta.delta:+.{DECIMALS}f} | {_direction(delta.delta)} "
        f"| {STATUS_REGRESSED if delta.regressed else STATUS_OK} |"
        for delta in report.deltas
    ]
    return "\n".join(
        [
            header,
            "",
            f"Verdict: **{verdict}**",
            "",
            "| metric | baseline | current | delta | dir | status |",
            "| --- | --- | --- | --- | --- | --- |",
            *rows,
            "",
            f"Newly failing cases: {_case_list(report.newly_failing_cases)}",
            f"Newly passing cases: {_case_list(report.newly_passing_cases)}",
            *(
                ["", "Not comparable: " + "; ".join(report.incomparable_reasons)]
                if report.incomparable_reasons
                else []
            ),
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Print the comparison and return 1 when the gate should fail."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True, help="baseline scorecard JSON")
    parser.add_argument("--current", type=Path, required=True, help="current scorecard JSON")
    args = parser.parse_args(argv)
    report = compare(load_scorecard(args.baseline), load_scorecard(args.current), Tolerances())
    print(render_comparison_markdown(report))
    return EXIT_REGRESSED if report.regressed else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
