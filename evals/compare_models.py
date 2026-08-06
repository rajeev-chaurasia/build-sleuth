"""Score several models over the same cases and rank them.

Run as `python -m evals.compare_models --models a,b,c`.

Choosing a model on one number is how you end up with a fast model that is
wrong, or an accurate one that never finishes. This prints every axis side by
side and refuses to rank models whose coverage differs, since a model scored
on fewer cases is not comparable to one scored on all of them.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path

from buildsleuth.config import load_settings
from buildsleuth.llm.types import ModelError
from evals.run_eval import (
    DEFAULT_DATASET_DIR,
    DEFAULT_RESULTS_DIR,
    attach_cost,
    build_classifier,
)
from evals.runner import NO_PROMPT_HASH, evaluate
from evals.scorecard import Scorecard, save_scorecard

DECIMALS = 3
FULL_COVERAGE = 1.0
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ModelRow:
    """One model's line in the comparison, or why it has none."""

    model: str
    coverage: float
    evaluated: int
    total: int
    accuracy: float
    macro_f1: float
    cost_weighted_error: float
    subcategory_accuracy: float
    usd_per_triage: float
    seconds: float
    error: str | None = None

    @property
    def complete(self) -> bool:
        return self.error is None and self.coverage >= FULL_COVERAGE


def row_from_scorecard(card: Scorecard) -> ModelRow:
    total = len(card.per_case)
    answered = [case for case in card.per_case if case.predicted_class is not None]
    report = card.classification
    subcategory = (
        sum(case.subcategory_correct for case in answered) / len(answered) if answered else 0.0
    )
    return ModelRow(
        model=card.model,
        coverage=len(answered) / total if total else 0.0,
        evaluated=len(answered),
        total=total,
        accuracy=report.accuracy if report else 0.0,
        macro_f1=report.macro_f1 if report else 0.0,
        cost_weighted_error=report.cost_weighted_error if report else 0.0,
        subcategory_accuracy=subcategory,
        usd_per_triage=card.cost.list_price_usd / total if total else 0.0,
        seconds=card.cost.wall_seconds,
    )


def failed_row(model: str, error: str) -> ModelRow:
    return ModelRow(model, 0.0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, error=error)


def render_comparison(rows: list[ModelRow]) -> str:
    """Table plus an explicit verdict, including what could not be compared."""
    header = (
        "| model | coverage | accuracy | macro F1 | cost weighted error"
        " | subcategory | usd per triage | seconds |"
    )
    lines = [
        "### Model comparison",
        "",
        header,
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for row in sorted(rows, key=lambda item: (-item.coverage, -item.macro_f1)):
        if row.error is not None:
            lines.append(f"| `{row.model}` | {UNAVAILABLE} | | | | | | |")
            continue
        lines.append(
            f"| `{row.model}` | {row.evaluated}/{row.total} | {row.accuracy:.{DECIMALS}f} "
            f"| {row.macro_f1:.{DECIMALS}f} | {row.cost_weighted_error:.{DECIMALS}f} "
            f"| {row.subcategory_accuracy:.{DECIMALS}f} | {row.usd_per_triage:.4f} "
            f"| {row.seconds:.1f} |"
        )

    lines += ["", *_verdict(rows)]
    return "\n".join(lines)


def _verdict(rows: list[ModelRow]) -> list[str]:
    complete = [row for row in rows if row.complete]
    partial = [row for row in rows if row.error is None and not row.complete]
    failed = [row for row in rows if row.error is not None]

    notes: list[str] = []
    if complete:
        best_quality = max(complete, key=lambda row: row.macro_f1)
        safest = min(complete, key=lambda row: row.cost_weighted_error)
        fastest = min(complete, key=lambda row: row.seconds)
        notes.append(
            f"Best macro F1: `{best_quality.model}` ({best_quality.macro_f1:.{DECIMALS}f})"
        )
        notes.append(
            f"Fewest expensive mistakes: `{safest.model}`"
            f" (cost weighted error {safest.cost_weighted_error:.{DECIMALS}f})"
        )
        notes.append(f"Fastest: `{fastest.model}` ({fastest.seconds:.1f}s)")
        if best_quality.model != safest.model:
            notes.append(
                "The most accurate model is not the one that errs most safely,"
                " so pick on the axis your reviewers care about."
            )
    else:
        notes.append("No model answered every case, so nothing here is rankable.")

    for row in partial:
        notes.append(
            f"`{row.model}` answered only {row.evaluated} of {row.total} cases."
            " Its scores cover the cases it managed and are not comparable."
        )
    for row in failed:
        notes.append(f"`{row.model}` could not be scored: {row.error}")
    return notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", required=True, help="Comma separated model ids.")
    parser.add_argument("--subset", default=None)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    rows: list[ModelRow] = []

    for name in [item.strip() for item in args.models.split(",") if item.strip()]:
        try:
            classifier = build_classifier(name, args.dataset, args.subset, settings)
            prompt_hash = getattr(classifier, "prompt_hash", NO_PROMPT_HASH)
            card = evaluate(classifier, args.dataset, subset=args.subset, prompt_hash=prompt_hash)
        except (ModelError, SystemExit) as error:
            rows.append(failed_row(name, str(error)))
            continue

        attach_cost(card, classifier)
        rows.append(row_from_scorecard(card))
        if args.save:
            save_scorecard(card, args.results)

    print(render_comparison(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
