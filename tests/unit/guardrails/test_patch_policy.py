"""Limits on a generated patch, checked before anyone is asked to review it."""

import pytest

from buildsleuth.guardrails.patch_policy import (
    MAX_CHANGED_LINES,
    MAX_FILES,
    changed_line_count,
    check_patch,
    find_secret,
)
from buildsleuth.models.taxonomy import FailureClass

CODE = FailureClass.CODE_CHANGE
CONFIG = FailureClass.PIPELINE_CONFIG


def _patch_for(paths: list[str], added_lines: int = 1) -> str:
    blocks = []
    for path in paths:
        body = "\n".join(f"+line {index}" for index in range(added_lines))
        blocks.append(f"--- a/{path}\n+++ b/{path}\n@@ -1 +1,{added_lines} @@\n{body}")
    return "\n".join(blocks)


def test_a_small_patch_is_allowed() -> None:
    assert check_patch(_patch_for(["src/app.py"]), CODE) is None


def test_an_empty_patch_is_refused() -> None:
    assert check_patch("", CODE) is not None
    assert check_patch("   \n", CODE) is not None


def test_touching_too_many_files_is_refused() -> None:
    paths = [f"src/file_{index}.py" for index in range(MAX_FILES + 1)]
    violation = check_patch(_patch_for(paths), CODE)

    assert violation is not None
    assert str(MAX_FILES) in violation.reason


def test_exactly_the_file_limit_is_allowed() -> None:
    paths = [f"src/file_{index}.py" for index in range(MAX_FILES)]
    assert check_patch(_patch_for(paths), CODE) is None


def test_a_patch_too_large_to_review_is_refused() -> None:
    violation = check_patch(_patch_for(["src/app.py"], MAX_CHANGED_LINES + 1), CODE)

    assert violation is not None
    assert "needs a person to write it" in violation.reason


def test_changed_lines_ignore_the_diff_headers() -> None:
    """The +++ and --- headers are not edits and must not count toward the limit."""
    patch = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"
    assert changed_line_count(patch) == 2


@pytest.mark.parametrize(
    "secret",
    [
        "ghp_abcdefghijklmnopqrstuvwxyz012345",
        "AKIAIOSFODNN7EXAMPLE",
        "AIzaSyC1234567890abcdefghijklmnopqrs",
        "-----BEGIN RSA PRIVATE KEY-----",
        'api_key = "sk-not-a-real-key-here"',
    ],
)
def test_a_patch_carrying_a_credential_is_refused(secret: str) -> None:
    patch = f"--- a/config.py\n+++ b/config.py\n@@ -1 +1 @@\n+{secret}\n"
    violation = check_patch(patch, CODE)

    assert violation is not None
    assert "credential" in violation.reason


def test_ordinary_code_is_not_mistaken_for_a_credential() -> None:
    """A policy that fires on normal code trains people to ignore it."""
    patch = (
        "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n"
        "+def load_token(name: str) -> str:\n"
        '+    return os.environ["TOKEN"]\n'
    )
    assert find_secret(patch) is None
    assert check_patch(patch, CODE) is None


def test_editing_the_workflow_is_refused_for_a_code_failure() -> None:
    """A code bug must not be fixed by changing the checks that caught it."""
    violation = check_patch(_patch_for([".github/workflows/ci.yml"]), CODE)

    assert violation is not None
    assert "checks themselves" in violation.reason


def test_editing_the_workflow_is_allowed_for_a_pipeline_failure() -> None:
    assert check_patch(_patch_for([".github/workflows/ci.yml"]), CONFIG) is None
