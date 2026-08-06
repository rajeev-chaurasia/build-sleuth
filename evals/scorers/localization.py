"""Culprit-file localization scoring.

Ranked file lists come from an agent and truth lists come from the dataset, so
paths arrive in mixed shapes. Comparison always runs on a normalized form.
"""

from collections.abc import Sequence

from pydantic import BaseModel

from buildsleuth.models.triage import MAX_RANKED_FILES

TOP_K = 1
WIDE_K = MAX_RANKED_FILES
CURRENT_DIR_PREFIX = "./"


def normalize_path(path: str) -> str:
    """Canonical comparison form: forward slashes, no leading './'."""
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith(CURRENT_DIR_PREFIX):
        normalized = normalized[len(CURRENT_DIR_PREFIX) :]
    return normalized


def _normalized_set(paths: Sequence[str]) -> set[str]:
    return {normalize_path(path) for path in paths}


def hit_at_k(truth_files: Sequence[str], ranked: Sequence[str], k: int) -> bool:
    """True when any truth file appears in the first `k` ranked paths."""
    if k <= 0:
        return False
    truth = _normalized_set(truth_files)
    return any(normalize_path(path) in truth for path in ranked[:k])


def reciprocal_rank(truth_files: Sequence[str], ranked: Sequence[str]) -> float:
    """1 / rank of the first ranked path that is a truth file, else 0.0."""
    truth = _normalized_set(truth_files)
    for rank, path in enumerate(ranked, start=1):
        if normalize_path(path) in truth:
            return 1.0 / rank
    return 0.0


class LocalizationReport(BaseModel):
    hit_at_1: float
    hit_at_5: float
    mrr: float
    n_evaluated: int


def localization_metrics(
    samples: Sequence[tuple[Sequence[str], Sequence[str]]],
) -> LocalizationReport:
    """Aggregate hit@1, hit@5 and MRR over (truth_files, ranked) samples.

    Cases with an empty truth_files list are excluded from every metric: a pure
    infrastructure failure has no culprit file, so scoring localization on it
    would punish the correct behaviour of proposing nothing. `n_evaluated`
    reports how many cases actually contributed.
    """
    scored = [(truth, ranked) for truth, ranked in samples if truth]
    if not scored:
        return LocalizationReport(hit_at_1=0.0, hit_at_5=0.0, mrr=0.0, n_evaluated=0)

    n_evaluated = len(scored)
    hits_at_top = sum(1 for truth, ranked in scored if hit_at_k(truth, ranked, TOP_K))
    hits_at_wide = sum(1 for truth, ranked in scored if hit_at_k(truth, ranked, WIDE_K))
    rank_sum = sum(reciprocal_rank(truth, ranked) for truth, ranked in scored)
    return LocalizationReport(
        hit_at_1=hits_at_top / n_evaluated,
        hit_at_5=hits_at_wide / n_evaluated,
        mrr=rank_sum / n_evaluated,
        n_evaluated=n_evaluated,
    )
