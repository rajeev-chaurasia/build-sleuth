"""Run the same evaluation several times and report the spread.

Models are not deterministic even at temperature zero, so a single run is one
sample, not a measurement. A regression gate tuned tighter than the noise
fires on the noise, which is worse than having no gate at all: it trains
people to ignore it.
"""

import argparse
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
DEFAULT_TRIALS = 3
MIN_TRIALS_FOR_SPREAD = 2


@dataclass(frozen=True)
class MetricSpread:
    name: str
    values: list[float]

    @property
    def mean(self) -> float:
        return statistics.fmean(self.values)

    @property
    def low(self) -> float:
        return min(self.values)

    @property
    def high(self) -> float:
        return max(self.values)

    @property
    def spread(self) -> float:
        """Worst case swing between two runs of the same thing."""
        return self.high - self.low

    @property
    def stdev(self) -> float:
        if len(self.values) < MIN_TRIALS_FOR_SPREAD:
            return 0.0
        return statistics.stdev(self.values)


def _metric_values(cards: list[Scorecard]) -> dict[str, list[float]]:
    accuracy, macro_f1, cost_error, hit_at_1 = [], [], [], []
    for card in cards:
        if card.classification is not None:
            accuracy.append(card.classification.accuracy)
            macro_f1.append(card.classification.macro_f1)
            cost_error.append(card.classification.cost_weighted_error)
        if card.localization is not None:
            hit_at_1.append(card.localization.hit_at_1)
    return {
        "accuracy": accuracy,
        "macro_f1": macro_f1,
        "cost_weighted_error": cost_error,
        "hit_at_1": hit_at_1,
    }


def spreads(cards: list[Scorecard]) -> list[MetricSpread]:
    return [
        MetricSpread(name=name, values=values)
        for name, values in _metric_values(cards).items()
        if values
    ]


def render_trials(model: str, cards: list[Scorecard], measured: list[MetricSpread]) -> str:
    """Table of mean and spread, plus what the spread implies for the gate."""
    lines = [
        f"### Trial spread: `{model}` over {len(cards)} runs",
        "",
        "| metric | mean | low | high | spread | stdev |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for metric in measured:
        lines.append(
            f"| {metric.name} | {metric.mean:.{DECIMALS}f} | {metric.low:.{DECIMALS}f} "
            f"| {metric.high:.{DECIMALS}f} | {metric.spread:.{DECIMALS}f} "
            f"| {metric.stdev:.{DECIMALS}f} |"
        )

    lines += ["", *_gate_advice(measured)]
    return "\n".join(lines)


def _gate_advice(measured: list[MetricSpread]) -> list[str]:
    if not measured:
        return ["No metrics were produced, so there is nothing to say about the gate."]

    worst = max(measured, key=lambda metric: metric.spread)
    if worst.spread == 0.0:
        return ["Every run agreed exactly, so any tolerance above zero is safe."]
    return [
        f"Widest swing was {worst.name} at {worst.spread:.{DECIMALS}f} across runs that"
        " differed in nothing but model nondeterminism.",
        f"A regression tolerance below {worst.spread:.{DECIMALS}f} on that metric will fire"
        " on noise. Either raise the tolerance above the spread, or add cases until the"
        " spread falls below the tolerance you want.",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    parser.add_argument("--subset", default=None)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()

    settings = load_settings()
    cards: list[Scorecard] = []
    failures: list[str] = []

    for trial in range(1, args.trials + 1):
        try:
            classifier = build_classifier(args.model, args.dataset, args.subset, settings)
            prompt_hash: Any = getattr(classifier, "prompt_hash", NO_PROMPT_HASH)
            card = evaluate(classifier, args.dataset, subset=args.subset, prompt_hash=prompt_hash)
        except ModelError as error:
            # Losing trial three should not throw away trials one and two.
            failures.append(f"trial {trial}: {error}")
            break

        attach_cost(card, classifier)
        cards.append(card)
        if args.save:
            save_scorecard(card, args.results)

    if not cards:
        print("\n".join(["No trial completed.", *failures]))
        return 1

    print(render_trials(args.model, cards, spreads(cards)))
    for note in failures:
        print(f"\nStopped early: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
