"""The eval entry point: model selection, key handling and cost accounting.

These are the seams where a run can quietly measure the wrong thing: a
baseline fitted to the wrong subset, a model built without its key, or a
scorecard whose cost fields never got filled in.
"""

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from evals.baselines import (
    MAJORITY_MODEL_NAME,
    REGEX_MODEL_NAME,
    MajorityClassClassifier,
    RegexRuleClassifier,
)
from evals.llm_classifier import LlmClassifier
from evals.run_eval import MissingApiKeyError, attach_cost, build_classifier, main
from evals.scorecard import CostSummary, Scorecard, build_scorecard

from buildsleuth.config import Settings
from buildsleuth.dataset.loader import CASE_FILE_NAME, CASES_DIR_NAME
from buildsleuth.llm.registry import API_KEY_ENV_VARS, MODEL_REGISTRY, TOKENS_PER_MILLION, Provider
from buildsleuth.llm.types import CompletionRequest, CompletionResult, Usage
from buildsleuth.models.taxonomy import FailureClass

DATASET_DIR_NAME = "dataset"
RESULTS_DIR_NAME = "results"
LOG_NAME = "logs/failed_job.txt"
SMOKE_TAG = "smoke"
SMOKE_SUBSET = "smoke"

GEMINI_MODEL = "gemini-3.6-flash"
OLLAMA_MODEL = "ollama-llama3.1-8b"
UNREGISTERED_MODEL = "not-in-the-registry"
API_KEY = "test-key"
GEMINI_KEY_ENV_VAR = API_KEY_ENV_VARS[Provider.GEMINI]

# Aliases accepted alongside the canonical variable names, so a developer's
# exported key cannot make an isolation test pass or fail by accident.
KEY_ENV_ALIASES = ("BUILDSLEUTH_GOOGLE_API_KEY", "BUILDSLEUTH_OPEN_ROUTER_API_KEY")

INPUT_TOKENS = 1000
OUTPUT_TOKENS = 500
REPAIRS = 3
REQUESTS = 12

CODE_CHANGE = FailureClass.CODE_CHANGE
FLAKY_TEST = FailureClass.FLAKY_TEST
SUBCATEGORIES: dict[FailureClass, str] = {
    CODE_CHANGE: "test_assertion",
    FLAKY_TEST: "async_wait",
}


def _payload(case_id: str, failure_class: FailureClass, smoke: bool) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "title": f"case {case_id}",
        "inputs": {
            "repo": "acme/widget",
            "run_id": 1,
            "head_sha": "0" * 40,
            "failed_job_name": "tests",
            "log_files": [LOG_NAME],
        },
        "ground_truth": {
            "failure_class": failure_class.value,
            "subcategory": SUBCATEGORIES[failure_class],
            "related_to_diff": True,
        },
        "provenance": {
            "source": "synthetic",
            "labeling_method": "constructed",
            "verified_by_human": smoke,
            "snapshot_date": "2026-01-01",
        },
        "tags": [SMOKE_TAG] if smoke else [],
    }


def _write_case(
    dataset_dir: Path,
    case_id: str,
    *,
    failure_class: FailureClass = CODE_CHANGE,
    smoke: bool = False,
) -> None:
    case_dir = dataset_dir / CASES_DIR_NAME / case_id
    log_path = case_dir / LOG_NAME
    log_path.parent.mkdir(parents=True)
    (case_dir / CASE_FILE_NAME).write_text(
        json.dumps(_payload(case_id, failure_class, smoke)), encoding="utf-8"
    )
    log_path.write_text(f"FAILED {case_id}\n", encoding="utf-8", newline="\n")


@pytest.fixture
def dataset_dir(tmp_path: Path) -> Path:
    path = tmp_path / DATASET_DIR_NAME
    path.mkdir()
    _write_case(path, "c1")
    return path


def _isolated_settings(
    monkeypatch: pytest.MonkeyPatch, keys: dict[str, str] | None = None
) -> Settings:
    """Settings carrying only the keys a test sets, never a developer's .env."""
    for env_var in (*API_KEY_ENV_VARS.values(), *KEY_ENV_ALIASES):
        if env_var:
            monkeypatch.delenv(env_var, raising=False)
    for env_var, value in (keys or {}).items():
        monkeypatch.setenv(env_var, value)
    return Settings(_env_file=None)


class ExplodingClient:
    """Stands in for a real client and fails loudly if anything tries to call it."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.constructed = True

    def complete(self, request: CompletionRequest) -> CompletionResult:
        raise AssertionError("a unit test must not call a model")


def _llm_classifier(
    name: str, *, input_tokens: int = INPUT_TOKENS, output_tokens: int = OUTPUT_TOKENS
) -> LlmClassifier:
    classifier = LlmClassifier(ExplodingClient(), name)
    classifier.usage = Usage(input_tokens=input_tokens, output_tokens=output_tokens)
    classifier.repairs = REPAIRS
    return classifier


def _card(requests: int = REQUESTS) -> Scorecard:
    return build_scorecard(
        git_sha="abc1234",
        model="test-model",
        prompt_hash="p1",
        dataset_hash="d1",
        subset=SMOKE_SUBSET,
        per_case=[],
        cost=CostSummary(total_requests=requests),
    )


def _run_main(monkeypatch: pytest.MonkeyPatch, argv: Sequence[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["run_eval", *argv])
    return main()


def test_build_classifier_returns_the_regex_baseline(
    dataset_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    classifier = build_classifier(
        REGEX_MODEL_NAME, dataset_dir, None, _isolated_settings(monkeypatch)
    )

    assert isinstance(classifier, RegexRuleClassifier)
    assert classifier.name == REGEX_MODEL_NAME


def test_build_classifier_returns_the_majority_baseline(
    dataset_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    classifier = build_classifier(
        MAJORITY_MODEL_NAME, dataset_dir, None, _isolated_settings(monkeypatch)
    )

    assert isinstance(classifier, MajorityClassClassifier)
    assert classifier.name == MAJORITY_MODEL_NAME


def test_the_majority_baseline_is_fitted_to_the_requested_subset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A floor fitted to cases it is not scored on is not the floor for that run."""
    dataset_dir = tmp_path / DATASET_DIR_NAME
    dataset_dir.mkdir()
    for index in range(3):
        _write_case(dataset_dir, f"code-{index}", failure_class=CODE_CHANGE)
    for index in range(2):
        _write_case(dataset_dir, f"flake-{index}", failure_class=FLAKY_TEST, smoke=True)

    settings = _isolated_settings(monkeypatch)
    on_smoke = build_classifier(MAJORITY_MODEL_NAME, dataset_dir, SMOKE_SUBSET, settings)
    on_full = build_classifier(MAJORITY_MODEL_NAME, dataset_dir, None, settings)

    assert on_smoke.classify("any log").failure_class == FLAKY_TEST
    assert on_full.classify("any log").failure_class == CODE_CHANGE


def test_build_classifier_returns_an_llm_classifier_for_a_registry_model(
    dataset_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("evals.run_eval.OpenAICompatClient", ExplodingClient)
    settings = _isolated_settings(monkeypatch, {GEMINI_KEY_ENV_VAR: API_KEY})

    classifier = build_classifier(GEMINI_MODEL, dataset_dir, None, settings)

    assert isinstance(classifier, LlmClassifier)
    assert classifier.name == GEMINI_MODEL


def test_a_local_model_needs_no_api_key(dataset_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("evals.run_eval.OpenAICompatClient", ExplodingClient)

    classifier = build_classifier(OLLAMA_MODEL, dataset_dir, None, _isolated_settings(monkeypatch))

    assert isinstance(classifier, LlmClassifier)


def test_an_unknown_model_lists_the_valid_options(
    dataset_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        build_classifier(UNREGISTERED_MODEL, dataset_dir, None, _isolated_settings(monkeypatch))

    message = str(excinfo.value)
    assert UNREGISTERED_MODEL in message
    assert REGEX_MODEL_NAME in message
    assert MAJORITY_MODEL_NAME in message
    assert GEMINI_MODEL in message


def test_a_missing_api_key_names_the_variable_and_builds_no_client(
    dataset_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failing before the client is built keeps a keyless run from spending a request."""
    built: list[str] = []

    def _record(*args: Any, **kwargs: Any) -> ExplodingClient:
        built.append("constructed")
        return ExplodingClient()

    monkeypatch.setattr("evals.run_eval.OpenAICompatClient", _record)

    with pytest.raises(MissingApiKeyError) as excinfo:
        build_classifier(GEMINI_MODEL, dataset_dir, None, _isolated_settings(monkeypatch))

    message = str(excinfo.value)
    assert GEMINI_KEY_ENV_VAR in message
    assert GEMINI_MODEL in message
    assert built == []


def test_attach_cost_is_a_noop_for_a_baseline() -> None:
    card = _card()

    attach_cost(card, RegexRuleClassifier())

    assert card.cost.total_input_tokens == 0
    assert card.cost.total_output_tokens == 0
    assert card.cost.estimated_usd == pytest.approx(0.0)
    assert card.cost.schema_failure_rate == pytest.approx(0.0)


def test_attach_cost_folds_in_tokens_and_both_prices() -> None:
    card = _card()
    spec = MODEL_REGISTRY[GEMINI_MODEL]
    expected_list = (
        INPUT_TOKENS * spec.list_usd_per_million_input
        + OUTPUT_TOKENS * spec.list_usd_per_million_output
    ) / TOKENS_PER_MILLION

    attach_cost(card, _llm_classifier(GEMINI_MODEL))

    assert card.cost.total_input_tokens == INPUT_TOKENS
    assert card.cost.total_output_tokens == OUTPUT_TOKENS
    assert card.cost.estimated_usd == pytest.approx(0.0)  # free tier
    assert card.cost.list_price_usd == pytest.approx(expected_list)
    assert card.cost.list_price_usd > 0.0


def test_repairs_are_counted_not_turned_into_a_rate() -> None:
    """A repair is an answer that was reparsed, not a case with no answer.

    These were once the same field, and the repair figure silently replaced
    the runner's hard-failure rate, which flattered any model that failed.
    """
    card = _card(requests=REQUESTS)
    card.cost.schema_failure_rate = 0.5

    attach_cost(card, _llm_classifier(GEMINI_MODEL))

    assert card.cost.repairs == REPAIRS
    assert card.cost.schema_failure_rate == pytest.approx(0.5)


def test_a_run_with_no_requests_still_reports_its_repairs() -> None:
    card = _card(requests=0)

    attach_cost(card, _llm_classifier(GEMINI_MODEL))

    assert card.cost.repairs == REPAIRS


def test_attach_cost_skips_pricing_for_a_model_outside_the_registry() -> None:
    """Usage is still real even when no price list covers the model."""
    card = _card()

    attach_cost(card, _llm_classifier(UNREGISTERED_MODEL))

    assert card.cost.total_input_tokens == INPUT_TOKENS
    assert card.cost.estimated_usd == pytest.approx(0.0)
    assert card.cost.list_price_usd == pytest.approx(0.0)


def test_main_prints_a_scorecard(
    dataset_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = _run_main(monkeypatch, ["--model", REGEX_MODEL_NAME, "--dataset", str(dataset_dir)])

    assert exit_code == 0
    assert "BuildSleuth scorecard" in capsys.readouterr().out


def test_main_writes_the_scorecard_when_asked(
    tmp_path: Path,
    dataset_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    results_dir = tmp_path / RESULTS_DIR_NAME

    exit_code = _run_main(
        monkeypatch,
        [
            "--model",
            REGEX_MODEL_NAME,
            "--dataset",
            str(dataset_dir),
            "--results",
            str(results_dir),
            "--save",
            "--subset",
            SMOKE_SUBSET,
        ],
    )

    written = list(results_dir.glob("*.json"))
    assert exit_code == 0
    assert len(written) == 1
    assert str(written[0]) in capsys.readouterr().out


def test_main_starts_and_stops_tracing_only_when_asked(
    dataset_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("evals.run_eval.setup_tracing", lambda: calls.append("setup"))
    monkeypatch.setattr("evals.run_eval.shutdown_tracing", lambda: calls.append("shutdown"))

    _run_main(monkeypatch, ["--model", REGEX_MODEL_NAME, "--dataset", str(dataset_dir)])
    assert calls == []

    _run_main(monkeypatch, ["--model", REGEX_MODEL_NAME, "--dataset", str(dataset_dir), "--trace"])
    assert calls == ["setup", "shutdown"]
    capsys.readouterr()
