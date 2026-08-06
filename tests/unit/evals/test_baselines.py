"""Tests for the non-LLM baseline classifiers."""

import pytest
from evals.baselines import MajorityClassClassifier, RegexRuleClassifier

from buildsleuth.models.taxonomy import SUBCATEGORIES, FailureClass


@pytest.mark.parametrize(
    ("log_line", "expected"),
    [
        ("No space left on device", FailureClass.INFRA_ENVIRONMENT),
        ("java.lang.OutOfMemoryError: Java heap space", FailureClass.INFRA_ENVIRONMENT),
        ("npm ERR! ERESOLVE unable to resolve dependency tree", FailureClass.INFRA_ENVIRONMENT),
        ("Unable to resolve action actions/checkout@v99", FailureClass.PIPELINE_CONFIG),
        ("error[E0308]: mismatched types", FailureClass.CODE_CHANGE),
        ("FAILED tests/test_api.py::test_create", FailureClass.CODE_CHANGE),
    ],
)
def test_regex_rules_classify_known_signatures(log_line: str, expected: FailureClass) -> None:
    verdict = RegexRuleClassifier().classify(log_line)
    assert verdict.failure_class == expected


def test_regex_subcategory_is_always_valid_for_its_class() -> None:
    """A baseline that emits an invalid subcategory would poison the scorer."""
    classifier = RegexRuleClassifier()
    samples = [
        "No space left on device",
        "OutOfMemoryError",
        "Connection reset by peer",
        "The operation was canceled",
        "ERESOLVE could not resolve",
        "Input required and not supplied: token",
        "Invalid workflow file",
        "retrying after flaky test",
        "error[E0308]: mismatched types",
        "Incompatible types in assignment",
        "would reformat src/app.py",
        "FAILED tests/test_x.py::test_y",
        "nothing matches this line at all",
    ]
    for sample in samples:
        verdict = classifier.classify(sample)
        assert verdict.subcategory in SUBCATEGORIES[verdict.failure_class], sample


def test_infra_signal_wins_over_generic_test_failure() -> None:
    """Ordering matters: a disk-full run also prints test failures."""
    log = "FAILED tests/test_big.py::test_write\nNo space left on device\n"
    assert RegexRuleClassifier().classify(log).failure_class == FailureClass.INFRA_ENVIRONMENT


def test_unmatched_log_falls_back_without_raising() -> None:
    verdict = RegexRuleClassifier().classify("everything went fine, somehow")
    assert verdict.failure_class == FailureClass.CODE_CHANGE
    assert "no rule matched" in verdict.reasoning


def test_related_to_diff_requires_a_diff() -> None:
    classifier = RegexRuleClassifier()
    assert (
        classifier.classify("FAILED tests/test_x.py::test_y", diff_text=None).related_to_diff
        is False
    )
    assert (
        classifier.classify("FAILED tests/test_x.py::test_y", diff_text="patch").related_to_diff
        is True
    )
    assert (
        classifier.classify("No space left on device", diff_text="patch").related_to_diff is False
    )


def test_majority_baseline_uses_most_common_label() -> None:
    labels = [FailureClass.FLAKY_TEST, FailureClass.FLAKY_TEST, FailureClass.CODE_CHANGE]
    classifier = MajorityClassClassifier(labels)
    verdict = classifier.classify("any log at all")
    assert verdict.failure_class == FailureClass.FLAKY_TEST
    assert verdict.subcategory in SUBCATEGORIES[FailureClass.FLAKY_TEST]


def test_majority_baseline_without_labels_still_answers() -> None:
    verdict = MajorityClassClassifier().classify("any log")
    assert verdict.failure_class == FailureClass.CODE_CHANGE
