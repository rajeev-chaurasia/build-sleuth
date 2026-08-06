"""Failure taxonomy.

Categories follow the CI failure literature so labels are defensible rather
than invented: Vassallo et al. ICSME 2017 and Lou et al. FSE 2020 for build
failure categories, Luo et al. FSE 2014 and iDFlakies ICST 2019 for flaky
root causes, Rausch et al. MSR 2017 for infrastructure failures. The
related_to_diff flag follows Huang et al. 2026, who found developers spend a
median of four hours deciding whether a failure relates to their change.
"""

from enum import StrEnum


class FailureClass(StrEnum):
    CODE_CHANGE = "code_change"
    FLAKY_TEST = "flaky_test"
    INFRA_ENVIRONMENT = "infra_environment"
    PIPELINE_CONFIG = "pipeline_config"


SUBCATEGORIES: dict[FailureClass, frozenset[str]] = {
    FailureClass.CODE_CHANGE: frozenset(
        {
            "compile_error",
            "test_assertion",
            "type_check",
            "lint_or_format",
            "dependency_conflict",
        }
    ),
    FailureClass.FLAKY_TEST: frozenset(
        {
            "async_wait",
            "concurrency",
            "test_order_dependency",
            "randomness",
            "network_flakiness",
            "resource_leak",
        }
    ),
    FailureClass.INFRA_ENVIRONMENT: frozenset(
        {
            "runner_outage",
            "dependency_registry",
            "network_timeout",
            "oom_or_disk",
            "external_service",
        }
    ),
    FailureClass.PIPELINE_CONFIG: frozenset(
        {
            "yaml_syntax",
            "action_version",
            "permissions_or_secrets",
            "cache_misconfig",
            "matrix_or_trigger",
        }
    ),
}

# Misreading a real code bug as a flake hides a regression, which is worse than
# the reverse mistake of investigating a flake. Weights scale the error term in
# the cost-weighted metric; 1.0 is the neutral cost of any other confusion.
NEUTRAL_ERROR_COST = 1.0
ERROR_COSTS: dict[tuple[FailureClass, FailureClass], float] = {
    (FailureClass.CODE_CHANGE, FailureClass.FLAKY_TEST): 3.0,
    (FailureClass.CODE_CHANGE, FailureClass.INFRA_ENVIRONMENT): 2.0,
    (FailureClass.FLAKY_TEST, FailureClass.CODE_CHANGE): 1.5,
}


# Upper bound of the cost-weighted error metric, so readers know its scale.
MAX_ERROR_COST = max([NEUTRAL_ERROR_COST, *ERROR_COSTS.values()])


def is_valid_subcategory(failure_class: FailureClass, subcategory: str) -> bool:
    return subcategory in SUBCATEGORIES[failure_class]


def error_cost(truth: FailureClass, predicted: FailureClass) -> float:
    """Cost of predicting `predicted` when the truth is `truth`."""
    if truth == predicted:
        return 0.0
    return ERROR_COSTS.get((truth, predicted), NEUTRAL_ERROR_COST)
