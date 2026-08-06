"""Entry point for scoring a classifier: `python -m evals.run_eval`.

Lives in the evals package rather than the buildsleuth CLI to keep the
dependency one way: evals imports buildsleuth, never the reverse.
"""

import argparse
from pathlib import Path

from buildsleuth.config import Settings, load_settings
from buildsleuth.dataset.loader import load_cases
from buildsleuth.llm.client import OpenAICompatClient
from buildsleuth.llm.rate_limit import RateLimiter
from buildsleuth.llm.registry import (
    MODEL_REGISTRY,
    Provider,
    api_key_env_var,
    estimate_cost_usd,
    estimate_list_cost_usd,
    get_model_spec,
)
from buildsleuth.prompts.loader import load_prompt
from buildsleuth.telemetry.otel import setup_tracing, shutdown_tracing
from evals.baselines import (
    MAJORITY_MODEL_NAME,
    REGEX_MODEL_NAME,
    MajorityClassClassifier,
    RegexRuleClassifier,
)
from evals.llm_classifier import TRIAGE_PROMPT_NAME, LlmClassifier
from evals.runner import NO_PROMPT_HASH, Classifier, evaluate, truth_labels
from evals.scorecard import Scorecard, render_markdown, save_scorecard

DEFAULT_DATASET_DIR = Path("dataset")
DEFAULT_RESULTS_DIR = Path("results")
BASELINE_NAMES = (REGEX_MODEL_NAME, MAJORITY_MODEL_NAME)


class MissingApiKeyError(SystemExit):
    """The chosen model needs a key that is not in the environment."""


def build_classifier(
    name: str, dataset_dir: Path, subset: str | None, settings: Settings
) -> Classifier:
    """Build a baseline or a model-backed classifier from its id."""
    if name == MAJORITY_MODEL_NAME:
        return MajorityClassClassifier(truth_labels(load_cases(dataset_dir, subset=subset)))
    if name == REGEX_MODEL_NAME:
        return RegexRuleClassifier()
    if name in MODEL_REGISTRY:
        return _build_llm_classifier(name, settings)

    valid = ", ".join([*BASELINE_NAMES, *sorted(MODEL_REGISTRY)])
    raise SystemExit(f"unknown model {name!r}, expected one of: {valid}")


def _build_llm_classifier(name: str, settings: Settings) -> LlmClassifier:
    spec = get_model_spec(name)
    api_key = settings.api_key_for(spec.provider)

    if spec.provider is not Provider.OLLAMA and not api_key:
        env_var = api_key_env_var(spec.provider)
        raise MissingApiKeyError(
            f"{name} needs an API key. Put it in .env as {env_var}=<key>, or export it."
        )

    client = OpenAICompatClient(spec=spec, api_key=api_key, rate_limiter=RateLimiter.for_spec(spec))
    return LlmClassifier(client, name, prompt=load_prompt(TRIAGE_PROMPT_NAME))


def main() -> int:
    parser = argparse.ArgumentParser(description="Score a classifier over the case dataset.")
    parser.add_argument("--model", default=REGEX_MODEL_NAME)
    parser.add_argument("--subset", default=None, help="Case subset, for example smoke.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--save", action="store_true", help="Write the scorecard to results/.")
    parser.add_argument("--trace", action="store_true", help="Export OTLP traces.")
    args = parser.parse_args()

    settings = load_settings()
    if args.trace:
        setup_tracing()

    classifier = build_classifier(args.model, args.dataset, args.subset, settings)
    prompt_hash = getattr(classifier, "prompt_hash", NO_PROMPT_HASH)
    card = evaluate(classifier, args.dataset, subset=args.subset, prompt_hash=prompt_hash)
    attach_cost(card, classifier)

    print(render_markdown(card))
    if args.save:
        print(f"\nscorecard written to {save_scorecard(card, args.results)}")

    if args.trace:
        shutdown_tracing()
    return 0


def attach_cost(card: Scorecard, classifier: Classifier) -> None:
    """Fold real token usage into the scorecard. Baselines have none, so they are skipped."""
    if not isinstance(classifier, LlmClassifier):
        return

    usage = classifier.usage
    card.cost.total_input_tokens = usage.input_tokens
    card.cost.total_output_tokens = usage.output_tokens

    spec = MODEL_REGISTRY.get(classifier.name)
    if spec is not None:
        card.cost.estimated_usd = estimate_cost_usd(spec, usage)
        card.cost.list_price_usd = estimate_list_cost_usd(spec, usage)

    requests = card.cost.total_requests or 1
    card.cost.schema_failure_rate = classifier.repairs / requests


if __name__ == "__main__":
    raise SystemExit(main())
