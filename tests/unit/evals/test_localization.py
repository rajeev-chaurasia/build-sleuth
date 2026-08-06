"""Localization scorer tests with hand-derived expectations."""

import pytest
from evals.scorers.localization import (
    hit_at_k,
    localization_metrics,
    normalize_path,
    reciprocal_rank,
)

TRUTH = ["src/pkg/module.py"]
FOUR_MISSES = ["a.py", "b.py", "c.py", "d.py"]


def test_normalize_path_handles_backslashes_and_dot_prefix() -> None:
    assert normalize_path("src\\pkg\\module.py") == "src/pkg/module.py"
    assert normalize_path("./src/pkg/module.py") == "src/pkg/module.py"
    assert normalize_path(".\\src\\pkg\\module.py") == "src/pkg/module.py"
    assert normalize_path("././src/pkg/module.py") == "src/pkg/module.py"
    assert normalize_path("  src/pkg/module.py  ") == "src/pkg/module.py"


def test_hit_matches_across_path_shapes() -> None:
    assert hit_at_k(["src\\pkg\\module.py"], ["./src/pkg/module.py"], 1) is True
    assert reciprocal_rank(["./src/pkg/module.py"], ["src\\pkg\\module.py"]) == 1.0


def test_hit_at_k_boundary_at_exactly_position_five() -> None:
    ranked = [*FOUR_MISSES, "src/pkg/module.py"]  # truth sits at position 5
    assert hit_at_k(TRUTH, ranked, 1) is False
    assert hit_at_k(TRUTH, ranked, 4) is False
    assert hit_at_k(TRUTH, ranked, 5) is True


def test_hit_at_k_with_non_positive_k_is_false() -> None:
    assert hit_at_k(TRUTH, ["src/pkg/module.py"], 0) is False


def test_hit_at_k_with_no_candidates_is_false() -> None:
    assert hit_at_k(TRUTH, [], 5) is False


def test_reciprocal_rank_uses_the_first_hit() -> None:
    ranked = ["a.py", "b.py", "src/pkg/module.py", "src/pkg/module.py"]
    assert reciprocal_rank(TRUTH, ranked) == pytest.approx(0.3333333333333333)  # 1/3


def test_reciprocal_rank_is_zero_without_a_hit() -> None:
    assert reciprocal_rank(TRUTH, FOUR_MISSES) == 0.0


def test_empty_truth_cases_are_excluded_from_every_metric() -> None:
    samples: list[tuple[list[str], list[str]]] = [
        (["src/a.py"], ["src/a.py", "x.py"]),  # hit at rank 1
        (["src/b.py"], ["x.py", "y.py", "src/b.py"]),  # hit at rank 3
        ([], ["x.py", "y.py"]),  # pure infra failure, excluded
    ]
    report = localization_metrics(samples)
    assert report.n_evaluated == 2  # third sample dropped
    assert report.hit_at_1 == pytest.approx(0.5)  # 1 of 2
    assert report.hit_at_5 == pytest.approx(1.0)  # 2 of 2
    # (1/1 + 1/3) / 2 = (4/3) / 2 = 2/3
    assert report.mrr == pytest.approx(0.6666666666666666)


def test_all_empty_truth_yields_zero_evaluated() -> None:
    report = localization_metrics([([], ["x.py"]), ([], [])])
    assert report.n_evaluated == 0
    assert report.hit_at_1 == 0.0
    assert report.hit_at_5 == 0.0
    assert report.mrr == 0.0


def test_no_samples_yields_zero_evaluated() -> None:
    report = localization_metrics([])
    assert report.n_evaluated == 0
    assert report.mrr == 0.0


def test_hit_at_five_counts_a_hit_at_position_five_but_hit_at_one_does_not() -> None:
    samples: list[tuple[list[str], list[str]]] = [
        (TRUTH, [*FOUR_MISSES, "src/pkg/module.py"]),
    ]
    report = localization_metrics(samples)
    assert report.hit_at_1 == 0.0
    assert report.hit_at_5 == pytest.approx(1.0)
    assert report.mrr == pytest.approx(0.2)  # 1/5
    assert report.n_evaluated == 1
