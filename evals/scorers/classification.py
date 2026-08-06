"""Classification scoring for triage verdicts.

Everything here is a pure function over (truth, predicted) pairs, so the
headline numbers of the benchmark are reproducible without a model call.
"""

from collections.abc import Sequence

from pydantic import BaseModel

from buildsleuth.models.taxonomy import FailureClass, error_cost

ALL_CLASSES: tuple[FailureClass, ...] = tuple(FailureClass)


class ClassMetrics(BaseModel):
    precision: float
    recall: float
    f1: float
    support: int


class ConfusionRow(BaseModel):
    """One truth-class row with every predicted column filled, including zeros."""

    truth: FailureClass
    counts: dict[FailureClass, int]
    support: int


class ConfusionMatrix(BaseModel):
    """Prediction counts keyed by (truth, predicted).

    Held as a nested mapping rather than a tuple-keyed dict because pydantic
    cannot round-trip tuple dict keys through JSON and this model is persisted
    inside a scorecard.
    """

    counts: dict[FailureClass, dict[FailureClass, int]] = {}

    @classmethod
    def from_pairs(cls, pairs: Sequence[tuple[FailureClass, FailureClass]]) -> "ConfusionMatrix":
        """Tally pairs of (truth, predicted) into a matrix."""
        counts: dict[FailureClass, dict[FailureClass, int]] = {}
        for truth, predicted in pairs:
            row = counts.setdefault(truth, {})
            row[predicted] = row.get(predicted, 0) + 1
        return cls(counts=counts)

    def count(self, truth: FailureClass, predicted: FailureClass) -> int:
        """Number of cases whose true class is `truth` and prediction is `predicted`."""
        return self.counts.get(truth, {}).get(predicted, 0)

    def support(self, failure_class: FailureClass) -> int:
        """Number of cases whose true class is `failure_class`."""
        return sum(self.counts.get(failure_class, {}).values())

    def predicted_count(self, failure_class: FailureClass) -> int:
        """Number of cases predicted as `failure_class`, across all true classes."""
        return sum(row.get(failure_class, 0) for row in self.counts.values())

    def total(self) -> int:
        """Total number of tallied pairs."""
        return sum(self.support(failure_class) for failure_class in ALL_CLASSES)

    def as_rows(self) -> list[ConfusionRow]:
        """Dense matrix in taxonomy order, ready for table rendering."""
        return [
            ConfusionRow(
                truth=truth,
                counts={predicted: self.count(truth, predicted) for predicted in ALL_CLASSES},
                support=self.support(truth),
            )
            for truth in ALL_CLASSES
        ]


class ClassificationReport(BaseModel):
    per_class: dict[FailureClass, ClassMetrics]
    macro_f1: float
    accuracy: float
    confusion: ConfusionMatrix
    cost_weighted_error: float


def _ratio(numerator: float, denominator: float) -> float:
    """Safe division that reports 0.0 for an empty denominator instead of raising."""
    return numerator / denominator if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    """Harmonic mean of precision and recall, 0.0 when both are 0.0."""
    total = precision + recall
    return 2.0 * precision * recall / total if total else 0.0


def classification_metrics(
    pairs: Sequence[tuple[FailureClass, FailureClass]],
) -> ClassificationReport:
    """Score failure-class predictions.

    Each pair is (truth, predicted). Definitions:

    - precision = tp / (tp + fp), 0.0 when the denominator is 0
    - recall = tp / (tp + fn), 0.0 when the denominator is 0
    - f1 = harmonic mean of the two, 0.0 when precision + recall is 0
    - macro_f1 = unweighted mean of per-class f1 over every FailureClass member,
      including classes with zero support. This is the strict reading of macro
      averaging: a class the taxonomy defines but the eval subset never exercises
      still contributes 0.0 and drags the mean down. It makes small subsets look
      worse than a support-filtered macro would, which is deliberate, since the
      headline number should not silently improve just because a subset happens
      to omit a class.
    - accuracy = correct / total
    - cost_weighted_error = mean of taxonomy.error_cost(truth, predicted), which
      penalizes calling a real code bug a flake harder than the reverse.
    """
    confusion = ConfusionMatrix.from_pairs(pairs)
    per_class: dict[FailureClass, ClassMetrics] = {}
    for failure_class in ALL_CLASSES:
        true_positives = confusion.count(failure_class, failure_class)
        predicted_total = confusion.predicted_count(failure_class)  # tp + fp
        support = confusion.support(failure_class)  # tp + fn
        precision = _ratio(true_positives, predicted_total)
        recall = _ratio(true_positives, support)
        per_class[failure_class] = ClassMetrics(
            precision=precision,
            recall=recall,
            f1=_f1(precision, recall),
            support=support,
        )

    correct = sum(1 for truth, predicted in pairs if truth == predicted)
    total_cost = sum(error_cost(truth, predicted) for truth, predicted in pairs)
    return ClassificationReport(
        per_class=per_class,
        macro_f1=sum(metrics.f1 for metrics in per_class.values()) / len(ALL_CLASSES),
        accuracy=_ratio(correct, len(pairs)),
        confusion=confusion,
        cost_weighted_error=_ratio(total_cost, len(pairs)),
    )


def subcategory_accuracy(pairs: Sequence[tuple[str, str]]) -> float:
    """Exact-match accuracy over (truth, predicted) subcategory strings."""
    correct = sum(1 for truth, predicted in pairs if truth == predicted)
    return _ratio(correct, len(pairs))


def related_to_diff_metrics(pairs: Sequence[tuple[bool, bool]]) -> ClassMetrics:
    """Score the related-to-diff flag, treating True as the positive class."""
    true_positives = sum(1 for truth, predicted in pairs if truth and predicted)
    predicted_positive = sum(1 for _, predicted in pairs if predicted)
    support = sum(1 for truth, _ in pairs if truth)
    precision = _ratio(true_positives, predicted_positive)
    recall = _ratio(true_positives, support)
    return ClassMetrics(
        precision=precision,
        recall=recall,
        f1=_f1(precision, recall),
        support=support,
    )
