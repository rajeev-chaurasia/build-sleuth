"""Classification scorer tests.

Every expected value below was derived by hand from the fixed pair set and is
written as a literal. Nothing here recomputes an expectation with the function
under test.
"""

import pytest
from evals.scorers.classification import (
    ConfusionMatrix,
    classification_metrics,
    related_to_diff_metrics,
    subcategory_accuracy,
)

from buildsleuth.models.taxonomy import FailureClass

CODE = FailureClass.CODE_CHANGE
FLAKY = FailureClass.FLAKY_TEST
INFRA = FailureClass.INFRA_ENVIRONMENT
CONFIG = FailureClass.PIPELINE_CONFIG

# Ten fixed (truth, predicted) pairs. INFRA_ENVIRONMENT is never predicted
# (zero predictions) and PIPELINE_CONFIG is never the truth (zero support).
PAIRS: list[tuple[FailureClass, FailureClass]] = [
    (CODE, CODE),  # 1 correct
    (CODE, CODE),  # 2 correct
    (CODE, FLAKY),  # 3 cost 3.0
    (CODE, CONFIG),  # 4 cost 1.0
    (FLAKY, FLAKY),  # 5 correct
    (FLAKY, CODE),  # 6 cost 1.5
    (FLAKY, FLAKY),  # 7 correct
    (INFRA, CODE),  # 8 cost 1.0
    (INFRA, FLAKY),  # 9 cost 1.0
    (INFRA, CONFIG),  # 10 cost 1.0
]

# Confusion, rows are truth:
#          pred CODE  FLAKY  INFRA  CONFIG   support
# CODE          2      1      0      1         4
# FLAKY         1      2      0      0         3
# INFRA         1      1      0      1         3
# CONFIG        0      0      0      0         0
# predicted totals: CODE 4, FLAKY 4, INFRA 0, CONFIG 2


def test_confusion_matrix_counts_and_support() -> None:
    confusion = ConfusionMatrix.from_pairs(PAIRS)
    assert confusion.count(CODE, CODE) == 2
    assert confusion.count(CODE, FLAKY) == 1
    assert confusion.count(CODE, CONFIG) == 1
    assert confusion.count(CODE, INFRA) == 0
    assert confusion.count(FLAKY, CODE) == 1
    assert confusion.count(FLAKY, FLAKY) == 2
    assert confusion.count(INFRA, CODE) == 1
    assert confusion.count(INFRA, FLAKY) == 1
    assert confusion.count(INFRA, CONFIG) == 1
    assert confusion.count(CONFIG, CONFIG) == 0
    assert confusion.support(CODE) == 4
    assert confusion.support(FLAKY) == 3
    assert confusion.support(INFRA) == 3
    assert confusion.support(CONFIG) == 0
    assert confusion.predicted_count(CODE) == 4
    assert confusion.predicted_count(FLAKY) == 4
    assert confusion.predicted_count(INFRA) == 0
    assert confusion.predicted_count(CONFIG) == 2
    assert confusion.total() == 10


def test_as_rows_is_dense_and_in_taxonomy_order() -> None:
    rows = ConfusionMatrix.from_pairs(PAIRS).as_rows()
    assert [row.truth for row in rows] == [CODE, FLAKY, INFRA, CONFIG]
    assert [list(row.counts.values()) for row in rows] == [
        [2, 1, 0, 1],
        [1, 2, 0, 0],
        [1, 1, 0, 1],
        [0, 0, 0, 0],
    ]
    assert [row.support for row in rows] == [4, 3, 3, 0]


def test_code_change_metrics() -> None:
    metrics = classification_metrics(PAIRS).per_class[CODE]
    # tp 2, fp 2 (FLAKY->CODE, INFRA->CODE), fn 2 (CODE->FLAKY, CODE->CONFIG)
    assert metrics.precision == pytest.approx(0.5)  # 2 / (2 + 2)
    assert metrics.recall == pytest.approx(0.5)  # 2 / (2 + 2)
    assert metrics.f1 == pytest.approx(0.5)  # 2 * .5 * .5 / (.5 + .5)
    assert metrics.support == 4


def test_flaky_test_metrics() -> None:
    metrics = classification_metrics(PAIRS).per_class[FLAKY]
    # tp 2, fp 2 (CODE->FLAKY, INFRA->FLAKY), fn 1 (FLAKY->CODE)
    assert metrics.precision == pytest.approx(0.5)  # 2 / (2 + 2)
    assert metrics.recall == pytest.approx(0.6666666666666666)  # 2 / (2 + 1) = 2/3
    # 2 * (1/2) * (2/3) / (1/2 + 2/3) = (2/3) / (7/6) = 4/7
    assert metrics.f1 == pytest.approx(0.5714285714285714)
    assert metrics.support == 3


def test_class_with_zero_predictions_scores_zero() -> None:
    metrics = classification_metrics(PAIRS).per_class[INFRA]
    # tp 0, fp 0 so precision divides by zero and is defined as 0.0
    assert metrics.precision == 0.0
    assert metrics.recall == 0.0  # 0 / 3
    assert metrics.f1 == 0.0
    assert metrics.support == 3


def test_class_with_zero_support_scores_zero() -> None:
    metrics = classification_metrics(PAIRS).per_class[CONFIG]
    assert metrics.precision == 0.0  # 0 / (0 + 2)
    # tp 0, fn 0 so recall divides by zero and is defined as 0.0
    assert metrics.recall == 0.0
    assert metrics.f1 == 0.0
    assert metrics.support == 0


def test_macro_f1_averages_over_all_classes_including_empty_ones() -> None:
    report = classification_metrics(PAIRS)
    # (1/2 + 4/7 + 0 + 0) / 4 = (15/14) / 4 = 15/56
    assert report.macro_f1 == pytest.approx(0.26785714285714285)


def test_accuracy() -> None:
    # correct pairs are 1, 2, 5, 7 => 4 / 10
    assert classification_metrics(PAIRS).accuracy == pytest.approx(0.4)


def test_cost_weighted_error() -> None:
    # 3.0 + 1.0 + 1.5 + 1.0 + 1.0 + 1.0 = 8.5 over 10 pairs
    assert classification_metrics(PAIRS).cost_weighted_error == pytest.approx(0.85)


def test_empty_pairs_are_all_zero() -> None:
    report = classification_metrics([])
    assert report.accuracy == 0.0
    assert report.macro_f1 == 0.0
    assert report.cost_weighted_error == 0.0
    assert report.confusion.total() == 0
    assert set(report.per_class) == {CODE, FLAKY, INFRA, CONFIG}


def test_perfect_run_scores_one() -> None:
    perfect = [(cls, cls) for cls in (CODE, FLAKY, INFRA, CONFIG)]
    report = classification_metrics(perfect)
    assert report.accuracy == pytest.approx(1.0)
    assert report.macro_f1 == pytest.approx(1.0)
    assert report.cost_weighted_error == 0.0


def test_subcategory_accuracy() -> None:
    pairs = [
        ("compile_error", "compile_error"),
        ("compile_error", "type_check"),
        ("async_wait", "async_wait"),
        ("oom_or_disk", "network_timeout"),
    ]
    assert subcategory_accuracy(pairs) == pytest.approx(0.5)  # 2 / 4
    assert subcategory_accuracy([]) == 0.0


def test_related_to_diff_metrics() -> None:
    pairs = [
        (True, True),
        (True, True),
        (True, False),
        (False, True),
        (False, False),
        (False, False),
    ]
    metrics = related_to_diff_metrics(pairs)
    # tp 2, fp 1, fn 1, support 3
    assert metrics.precision == pytest.approx(0.6666666666666666)  # 2 / 3
    assert metrics.recall == pytest.approx(0.6666666666666666)  # 2 / 3
    assert metrics.f1 == pytest.approx(0.6666666666666666)  # equal p and r
    assert metrics.support == 3


def test_related_to_diff_with_no_positive_predictions() -> None:
    metrics = related_to_diff_metrics([(True, False), (False, False)])
    assert metrics.precision == 0.0
    assert metrics.recall == 0.0  # 0 / 1
    assert metrics.f1 == 0.0
    assert metrics.support == 1
