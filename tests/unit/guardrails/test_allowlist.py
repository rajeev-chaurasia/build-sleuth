"""The allowlist is the reason this agent cannot write to a stranger's repository."""

import pytest

from buildsleuth.config import Settings
from buildsleuth.guardrails.allowlist import (
    GuardrailViolation,
    allowed_repos,
    check_write_target,
)

TARGET = "rajeev-chaurasia/build-sleuth"


def _settings(allowlist: str) -> Settings:
    return Settings(_env_file=None, pr_allowlist=allowlist)


def test_nothing_is_writable_by_default() -> None:
    """A fresh checkout must not be able to open a pull request anywhere."""
    with pytest.raises(GuardrailViolation, match="no repository is allowed"):
        check_write_target(TARGET, _settings(""))


def test_an_allowed_repository_passes() -> None:
    check_write_target(TARGET, _settings(TARGET))


def test_a_repository_not_on_the_list_is_refused() -> None:
    with pytest.raises(GuardrailViolation, match="not in the allowlist"):
        check_write_target("someone-else/their-repo", _settings(TARGET))


def test_the_refusal_names_what_was_allowed() -> None:
    """A guardrail that fails without saying why gets disabled by whoever hits it."""
    with pytest.raises(GuardrailViolation) as error:
        check_write_target("other/repo", _settings(TARGET))
    assert TARGET in str(error.value)


def test_matching_ignores_case_and_padding() -> None:
    check_write_target("Rajeev-Chaurasia/Build-Sleuth", _settings(f"  {TARGET} , other/repo "))


def test_several_repositories_can_be_allowed() -> None:
    settings = _settings("a/one,b/two,c/three")
    assert allowed_repos(settings) == {"a/one", "b/two", "c/three"}
    check_write_target("b/two", settings)


def test_empty_entries_are_ignored_rather_than_allowing_everything() -> None:
    """A trailing comma must not turn into a wildcard."""
    settings = _settings("a/one,,")
    assert allowed_repos(settings) == {"a/one"}
    with pytest.raises(GuardrailViolation):
        check_write_target("", settings)


def test_a_prefix_of_an_allowed_name_is_still_refused() -> None:
    with pytest.raises(GuardrailViolation):
        check_write_target("rajeev-chaurasia/build", _settings(TARGET))
