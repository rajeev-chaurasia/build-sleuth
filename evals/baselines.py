"""Non-LLM baselines.

Every model number in the scorecard is meaningless without a floor to compare
against. These classifiers use no LLM at all, so they cost nothing and are
deterministic. A model that cannot beat the regex rules is not earning its
tokens.
"""

import re
from collections import Counter
from dataclasses import dataclass

from buildsleuth.models.taxonomy import SUBCATEGORIES, FailureClass
from buildsleuth.models.triage import TriageVerdict

BASELINE_CONFIDENCE = 1.0
MAJORITY_MODEL_NAME = "baseline-majority"
REGEX_MODEL_NAME = "baseline-regex"

# Assumed when nothing matches. Code changes are the most common real cause,
# so this is also what the majority baseline usually degenerates to.
_FALLBACK_CLASS = FailureClass.CODE_CHANGE
_FALLBACK_SUBCATEGORY = "test_assertion"


@dataclass(frozen=True)
class _Rule:
    """One regex vote for a class and subcategory."""

    pattern: re.Pattern[str]
    failure_class: FailureClass
    subcategory: str


def _rule(pattern: str, failure_class: FailureClass, subcategory: str) -> _Rule:
    return _Rule(re.compile(pattern, re.MULTILINE), failure_class, subcategory)


# Ordered by specificity. The first match wins, so infrastructure and config
# signals are checked before the generic test-failure patterns they can hide
# behind.
RULES: tuple[_Rule, ...] = (
    _rule(r"No space left on device", FailureClass.INFRA_ENVIRONMENT, "oom_or_disk"),
    _rule(
        r"OutOfMemoryError|MemoryError|exit code 137", FailureClass.INFRA_ENVIRONMENT, "oom_or_disk"
    ),
    _rule(
        r"Could not resolve host|Connection reset|TLS handshake (?:timeout|failure|error)",
        FailureClass.INFRA_ENVIRONMENT,
        "network_timeout",
    ),
    _rule(
        r"The operation was canceled|The job running on runner .* has become unhealthy",
        FailureClass.INFRA_ENVIRONMENT,
        "runner_outage",
    ),
    _rule(
        r"ERESOLVE|ResolutionImpossible|Could not find a version|npm ERR! 404",
        FailureClass.INFRA_ENVIRONMENT,
        "dependency_registry",
    ),
    _rule(
        r"Unable to resolve action|Input required and not supplied|Secret .* not found",
        FailureClass.PIPELINE_CONFIG,
        "permissions_or_secrets",
    ),
    _rule(
        r"yaml: line \d+|Invalid workflow file|workflow is not valid",
        FailureClass.PIPELINE_CONFIG,
        "yaml_syntax",
    ),
    _rule(r"Flaky|flaky test|Rerun|retrying", FailureClass.FLAKY_TEST, "network_flakiness"),
    _rule(
        r"error\[E\d+\]|SyntaxError|error TS\d+|: error:|cannot find symbol",
        FailureClass.CODE_CHANGE,
        "compile_error",
    ),
    _rule(r"mypy|type error|Incompatible types", FailureClass.CODE_CHANGE, "type_check"),
    _rule(r"ruff|flake8|black|would reformat|lint", FailureClass.CODE_CHANGE, "lint_or_format"),
    _rule(r"^FAILED |AssertionError|^E\s+assert", FailureClass.CODE_CHANGE, "test_assertion"),
)


class RegexRuleClassifier:
    """Deterministic first-match-wins classifier over the condensed log."""

    name = REGEX_MODEL_NAME

    def classify(self, log_text: str, diff_text: str | None = None) -> TriageVerdict:
        for rule in RULES:
            if rule.pattern.search(log_text):
                return TriageVerdict(
                    failure_class=rule.failure_class,
                    subcategory=rule.subcategory,
                    related_to_diff=_guess_related_to_diff(rule.failure_class, diff_text),
                    confidence=BASELINE_CONFIDENCE,
                    evidence=[],
                    reasoning=f"matched rule for {rule.failure_class}/{rule.subcategory}",
                )
        return TriageVerdict(
            failure_class=_FALLBACK_CLASS,
            subcategory=_FALLBACK_SUBCATEGORY,
            related_to_diff=diff_text is not None,
            confidence=BASELINE_CONFIDENCE,
            evidence=[],
            reasoning="no rule matched, fell back to the most common class",
        )


class MajorityClassClassifier:
    """Always predicts the class that dominates the training labels it was given."""

    name = MAJORITY_MODEL_NAME

    def __init__(self, labels: list[FailureClass] | None = None) -> None:
        counts = Counter(labels or [])
        self._majority = counts.most_common(1)[0][0] if counts else _FALLBACK_CLASS

    def classify(self, log_text: str, diff_text: str | None = None) -> TriageVerdict:
        subcategory = (
            _FALLBACK_SUBCATEGORY
            if self._majority == _FALLBACK_CLASS
            else min(SUBCATEGORIES[self._majority])
        )
        return TriageVerdict(
            failure_class=self._majority,
            subcategory=subcategory,
            related_to_diff=False,
            confidence=BASELINE_CONFIDENCE,
            evidence=[],
            reasoning="majority class baseline",
        )


def _guess_related_to_diff(failure_class: FailureClass, diff_text: str | None) -> bool:
    """Infrastructure and flake failures are assumed unrelated to the patch."""
    if diff_text is None:
        return False
    return failure_class in (FailureClass.CODE_CHANGE, FailureClass.PIPELINE_CONFIG)
