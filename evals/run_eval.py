"""Entry point for scoring a classifier: `python -m evals.run_eval`.

Lives in the evals package rather than the buildsleuth CLI to keep the
dependency one way: evals imports buildsleuth, never the reverse.
"""

import argparse
from pathlib import Path

from buildsleuth.dataset.loader import load_cases
from evals.baselines import (
    MAJORITY_MODEL_NAME,
    REGEX_MODEL_NAME,
    MajorityClassClassifier,
    RegexRuleClassifier,
)
from evals.runner import Classifier, evaluate, truth_labels
from evals.scorecard import render_markdown, save_scorecard

DEFAULT_DATASET_DIR = Path("dataset")
DEFAULT_RESULTS_DIR = Path("results")


def build_classifier(name: str, dataset_dir: Path, subset: str | None) -> Classifier:
    if name == MAJORITY_MODEL_NAME:
        return MajorityClassClassifier(truth_labels(load_cases(dataset_dir, subset=subset)))
    if name == REGEX_MODEL_NAME:
        return RegexRuleClassifier()
    raise SystemExit(
        f"unknown model {name!r}, expected one of: {REGEX_MODEL_NAME}, {MAJORITY_MODEL_NAME}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Score a classifier over the case dataset.")
    parser.add_argument("--model", default=REGEX_MODEL_NAME)
    parser.add_argument("--subset", default=None, help="Case subset, for example smoke.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--save", action="store_true", help="Write the scorecard to results/.")
    args = parser.parse_args()

    classifier = build_classifier(args.model, args.dataset, args.subset)
    card = evaluate(classifier, args.dataset, subset=args.subset)
    print(render_markdown(card))

    if args.save:
        path = save_scorecard(card, args.results)
        print(f"\nscorecard written to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
