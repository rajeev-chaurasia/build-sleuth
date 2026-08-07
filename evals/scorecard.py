"""Scorecard: the versioned, on-disk record of one eval run.

A scorecard is the unit the regression gate compares and the README quotes. It
pins the code, the model, the prompt and the dataset it came from, so a number
is never quotable without the four things that produced it.
"""

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from pydantic import BaseModel, model_validator

from buildsleuth.models.taxonomy import MAX_ERROR_COST, FailureClass
from evals.scorers.classification import ClassificationReport, classification_metrics
from evals.scorers.localization import LocalizationReport, localization_metrics

SCHEMA_VERSION = 1
DECIMALS = 3
UNSAFE_MODEL_CHARS = re.compile(r"[/\\:]")
FILENAME_SAFE_SEPARATOR = "-"
SCORECARD_SUFFIX = ".json"
JSON_INDENT = 2
NONE_MARKER = "none"


class CostSummary(BaseModel):
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_requests: int = 0
    wall_seconds: float = 0.0
    estimated_usd: float = 0.0
    # What the same run would cost at the provider's paid rate. Free-tier runs
    # bill nothing, and "$0.00 per triage" alone tells a reader very little.
    list_price_usd: float = 0.0
    # Two different things, kept apart because they were once the same field
    # and the second silently overwrote the first. A repair is an answer that
    # was reparsed successfully; a hard failure is a case with no verdict at
    # all. Reporting one under the other's name flatters a model that failed.
    schema_failure_rate: float = 0.0
    repairs: int = 0


class CaseResult(BaseModel):
    """Per-case outcome. `error` is set when the case never produced a verdict."""

    case_id: str
    truth_class: FailureClass
    predicted_class: FailureClass | None = None
    correct: bool = False
    subcategory_correct: bool = False
    related_to_diff_correct: bool | None = False
    localization_rank: int | None = None
    latency_ms: float | None = None
    error: str | None = None

    @model_validator(mode="after")
    def check_missing_verdict_is_explained(self) -> Self:
        """A dropped case must carry a reason, or it vanishes from every metric silently."""
        if self.predicted_class is None and not self.error:
            raise ValueError(f"case {self.case_id} has no verdict and no error explaining why")
        if self.predicted_class is None and self.correct:
            raise ValueError(f"case {self.case_id} cannot be correct without a verdict")
        return self


class Scorecard(BaseModel):
    schema_version: int = SCHEMA_VERSION
    git_sha: str
    model: str
    prompt_hash: str
    dataset_hash: str
    subset: str
    created_at: datetime
    classification: ClassificationReport | None = None
    localization: LocalizationReport | None = None
    cost: CostSummary = CostSummary()
    per_case: list[CaseResult] = []


def build_scorecard(
    *,
    git_sha: str,
    model: str,
    prompt_hash: str,
    dataset_hash: str,
    subset: str,
    per_case: Sequence[CaseResult],
    localization_samples: Sequence[tuple[Sequence[str], Sequence[str]]] = (),
    cost: CostSummary | None = None,
    created_at: datetime | None = None,
) -> Scorecard:
    """Assemble a scorecard, deriving the reports from the per-case results.

    Classification pairs come from the cases that produced a verdict; a case
    that errored out has no predicted class and is excluded from classification
    rather than counted as a wrong answer. Localization needs the truth and
    ranked path lists, which do not fit in a CaseResult, so they are passed in
    as raw samples aligned with whatever subset was localized.
    """
    pairs: list[tuple[FailureClass, FailureClass]] = []
    for case in per_case:
        if case.predicted_class is not None:
            pairs.append((case.truth_class, case.predicted_class))
    return Scorecard(
        git_sha=git_sha,
        model=model,
        prompt_hash=prompt_hash,
        dataset_hash=dataset_hash,
        subset=subset,
        created_at=created_at if created_at is not None else datetime.now(UTC),
        classification=classification_metrics(pairs) if pairs else None,
        localization=localization_metrics(localization_samples) if localization_samples else None,
        cost=cost if cost is not None else CostSummary(),
        per_case=list(per_case),
    )


def scorecard_filename(card: Scorecard) -> str:
    """Stable filename identifying the code, model and prompt behind the numbers."""
    model = UNSAFE_MODEL_CHARS.sub(FILENAME_SAFE_SEPARATOR, card.model)
    return f"{card.git_sha}-{model}-{card.prompt_hash}{SCORECARD_SUFFIX}"


def save_scorecard(card: Scorecard, results_dir: Path) -> Path:
    """Write the scorecard as JSON under `results_dir` and return the path."""
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / scorecard_filename(card)
    path.write_text(card.model_dump_json(indent=JSON_INDENT), encoding="utf-8")
    return path


def load_scorecard(path: Path) -> Scorecard:
    """Read a scorecard back from JSON."""
    return Scorecard.model_validate_json(path.read_text(encoding="utf-8"))


def _num(value: float) -> str:
    return f"{value:.{DECIMALS}f}"


def _per_triage(card: Scorecard) -> float:
    """List-price cost divided across the cases actually triaged."""
    if not card.per_case:
        return 0.0
    return card.cost.list_price_usd / len(card.per_case)


def _answered(card: Scorecard) -> list[CaseResult]:
    return [case for case in card.per_case if case.predicted_class is not None]


def _subcategory_rate(card: Scorecard) -> float:
    """Share of answered cases whose subcategory also matched."""
    answered = _answered(card)
    if not answered:
        return 0.0
    return sum(case.subcategory_correct for case in answered) / len(answered)


def _related_to_diff_scored(card: Scorecard) -> list[CaseResult]:
    """Cases where the evidence can settle whether the failure follows the diff."""
    return [case for case in _answered(card) if case.related_to_diff_correct is not None]


def _related_to_diff_rate(card: Scorecard) -> float:
    """Share of scorable cases that correctly judged patch relatedness."""
    scored = _related_to_diff_scored(card)
    if not scored:
        return 0.0
    return sum(bool(case.related_to_diff_correct) for case in scored) / len(scored)


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    divider = ["---"] * len(header)
    return [
        f"| {' | '.join(header)} |",
        f"| {' | '.join(divider)} |",
        *(f"| {' | '.join(row)} |" for row in rows),
    ]


def render_markdown(card: Scorecard) -> str:
    """Compact markdown summary, sized for a README section or a PR comment."""
    lines = [
        f"### BuildSleuth scorecard: `{card.subset}`",
        "",
        *_table(
            ["model", "git sha", "prompt", "dataset", "created", "cases"],
            [
                [
                    f"`{card.model}`",
                    f"`{card.git_sha}`",
                    f"`{card.prompt_hash}`",
                    f"`{card.dataset_hash}`",
                    card.created_at.isoformat(timespec="seconds"),
                    str(len(card.per_case)),
                ]
            ],
        ),
        "",
    ]

    headline: list[Sequence[str]] = []
    if card.classification is not None:
        evaluated = card.classification.confusion.total()
        headline.append(["evaluated cases", f"{evaluated} of {len(card.per_case)}"])
        headline.append(["accuracy", _num(card.classification.accuracy)])
        # Averaged over all four classes, so a subset missing a class is
        # capped below 1.0 by design. Stated here so the number is not misread.
        headline.append(["macro f1 (all 4 classes)", _num(card.classification.macro_f1)])
        headline.append(
            [
                f"cost weighted error (0 to {MAX_ERROR_COST:.1f})",
                _num(card.classification.cost_weighted_error),
            ]
        )
        headline.append(["subcategory accuracy", _num(_subcategory_rate(card))])
        scorable = len(_related_to_diff_scored(card))
        headline.append(
            [
                f"related to diff accuracy ({scorable} scorable)",
                _num(_related_to_diff_rate(card)),
            ]
        )
    if card.localization is not None:
        headline.append(["hit@1", _num(card.localization.hit_at_1)])
        headline.append(["hit@5", _num(card.localization.hit_at_5)])
        headline.append(["mrr", _num(card.localization.mrr)])
        headline.append(["localized cases", str(card.localization.n_evaluated)])
    headline.append(["cases with no verdict", _num(card.cost.schema_failure_rate)])
    headline.append(["schema repairs", str(card.cost.repairs)])
    headline.append(["estimated usd", f"{card.cost.estimated_usd:.4f}"])
    headline.append(["usd at list price", f"{card.cost.list_price_usd:.4f}"])
    headline.append(["usd per triage at list price", f"{_per_triage(card):.4f}"])
    headline.append(
        ["tokens in / out", f"{card.cost.total_input_tokens} / {card.cost.total_output_tokens}"]
    )
    headline.append(["requests", str(card.cost.total_requests)])
    headline.append(["wall seconds", _num(card.cost.wall_seconds)])
    lines += _table(["metric", "value"], headline)

    if card.classification is not None:
        lines += ["", "#### Per class", ""]
        lines += _table(
            ["class", "precision", "recall", "f1", "support"],
            [
                [
                    failure_class.value,
                    _num(metrics.precision),
                    _num(metrics.recall),
                    _num(metrics.f1),
                    str(metrics.support),
                ]
                for failure_class, metrics in card.classification.per_class.items()
            ],
        )
        rows = card.classification.confusion.as_rows()
        columns = [column.value for column in rows[0].counts] if rows else []
        lines += ["", "#### Confusion (rows are truth)", ""]
        lines += _table(
            ["truth", *columns],
            [[row.truth.value, *(str(count) for count in row.counts.values())] for row in rows],
        )

    failures = [case.case_id for case in card.per_case if case.error]
    lines += ["", f"Cases with no verdict: {', '.join(failures) if failures else NONE_MARKER}"]
    return "\n".join(lines)
