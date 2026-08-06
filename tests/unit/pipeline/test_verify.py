"""Verify stage: the git apply gate, the level ordering, and patch path extraction.

check_applies runs against a real git repository built in tmp_path, because the
whole point of the level is that git, not a heuristic, decides whether a patch
is real.
"""

import subprocess
from pathlib import Path

import pytest

from buildsleuth.pipeline.verify import (
    EMPTY_PATCH_REASON,
    GIT,
    VerificationLevel,
    VerificationResult,
    check_applies,
    touched_paths,
)

TRACKED_FILE = "app.py"
ORIGINAL = "line one\nline two\nline three\n"
# Identity has to be inline so the test works with no global git config at all.
GIT_IDENTITY = (
    "-c",
    "user.email=test@example.com",
    "-c",
    "user.name=Test",
    "-c",
    "commit.gpgsign=false",
)
# Written into .git/config rather than passed with -c, so the git apply that
# check_applies runs sees it too. Without it the result depends on whatever
# core.autocrlf the developer has set globally.
NO_LINE_ENDING_CONVERSION = ("config", "core.autocrlf", "false")

GOOD_PATCH = (
    "diff --git a/app.py b/app.py\n"
    "--- a/app.py\n"
    "+++ b/app.py\n"
    "@@ -1,3 +1,3 @@\n"
    " line one\n"
    "-line two\n"
    "+line two patched\n"
    " line three\n"
)
WRONG_CONTEXT_PATCH = (
    "diff --git a/app.py b/app.py\n"
    "--- a/app.py\n"
    "+++ b/app.py\n"
    "@@ -1,3 +1,3 @@\n"
    " line one\n"
    "-a line that was never in the file\n"
    "+line two patched\n"
    " line three\n"
)
MISSING_FILE_PATCH = (
    "diff --git a/nowhere.py b/nowhere.py\n"
    "--- a/nowhere.py\n"
    "+++ b/nowhere.py\n"
    "@@ -1 +1 @@\n"
    "-before\n"
    "+after\n"
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [GIT, *GIT_IDENTITY, *args], cwd=repo, capture_output=True, text=True, check=True
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A one-commit git repository holding TRACKED_FILE with LF endings."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, *NO_LINE_ENDING_CONVERSION)
    (root / TRACKED_FILE).write_text(ORIGINAL, encoding="utf-8", newline="\n")
    _git(root, "add", TRACKED_FILE)
    _git(root, "commit", "-m", "initial")
    return root


def _status(repo: Path) -> str:
    return _git(repo, "status", "--porcelain").stdout


def test_a_correct_patch_applies(repo: Path) -> None:
    """The patch is an LF byte stream, so this also pins the CRLF-on-Windows regression."""
    result = check_applies(GOOD_PATCH, repo)

    assert result.level is VerificationLevel.APPLIES
    assert result.applied is True
    assert "applies cleanly" in result.detail


def test_wrong_context_lines_do_not_apply(repo: Path) -> None:
    """A model that invented the surrounding lines has to fail here, in milliseconds."""
    result = check_applies(WRONG_CONTEXT_PATCH, repo)

    assert result.level is VerificationLevel.NOTHING
    assert result.applied is False
    assert "does not apply" in result.detail
    assert TRACKED_FILE in result.stdout_tail


def test_a_patch_for_a_missing_file_does_not_apply(repo: Path) -> None:
    result = check_applies(MISSING_FILE_PATCH, repo)

    assert result.level is VerificationLevel.NOTHING
    assert result.stdout_tail != ""


@pytest.mark.parametrize("patch", ["", "   \n\t\n  "])
def test_a_blank_patch_is_rejected_without_running_git(repo: Path, patch: str) -> None:
    result = check_applies(patch, repo)

    assert result.level is VerificationLevel.NOTHING
    assert result.detail == EMPTY_PATCH_REASON
    assert result.stdout_tail == ""


@pytest.mark.parametrize("patch", [GOOD_PATCH, WRONG_CONTEXT_PATCH, MISSING_FILE_PATCH])
def test_checking_never_touches_the_working_tree(repo: Path, patch: str) -> None:
    """--check has to be side effect free, or a rejected patch would still dirty the clone."""
    check_applies(patch, repo)

    assert (repo / TRACKED_FILE).read_bytes().decode("utf-8") == ORIGINAL
    assert _status(repo) == ""


def test_levels_are_ordered_by_cost() -> None:
    assert (
        VerificationLevel.NOTHING
        < VerificationLevel.APPLIES
        < VerificationLevel.LINTS
        < VerificationLevel.FAILING_TEST_PASSES
        < VerificationLevel.NOTHING_ELSE_BROKE
    )


@pytest.mark.parametrize(
    ("level", "applied", "fixed"),
    [
        (VerificationLevel.NOTHING, False, False),
        (VerificationLevel.APPLIES, True, False),
        (VerificationLevel.LINTS, True, False),
        (VerificationLevel.FAILING_TEST_PASSES, True, True),
        (VerificationLevel.NOTHING_ELSE_BROKE, True, True),
    ],
)
def test_level_properties_turn_on_at_the_right_rung(
    level: VerificationLevel, applied: bool, fixed: bool
) -> None:
    result = VerificationResult(level, "detail")

    assert result.applied is applied
    assert result.fixed_the_failure is fixed


def test_touched_paths_reads_the_b_side_headers() -> None:
    patch = "+++ b/src/app.py\n+++ b/tests/test_app.py\n"

    assert touched_paths(patch) == ["src/app.py", "tests/test_app.py"]


def test_touched_paths_ignores_a_deletion_target() -> None:
    """A /dev/null b-side means the file is gone, so there is no path to guard."""
    patch = "--- a/src/gone.py\n+++ /dev/null\n+++ b/src/app.py\n"

    assert touched_paths(patch) == ["src/app.py"]


def test_touched_paths_deduplicates_and_preserves_order() -> None:
    patch = "+++ b/src/b.py\n+++ b/src/a.py\n+++ b/src/b.py\n"

    assert touched_paths(patch) == ["src/b.py", "src/a.py"]


def test_touched_paths_drops_a_timestamp_suffix() -> None:
    patch = "+++ b/src/app.py\t2026-01-01 12:00:00.000000000 +0000\n"

    assert touched_paths(patch) == ["src/app.py"]


def test_touched_paths_keeps_a_header_without_the_b_prefix() -> None:
    assert touched_paths("+++ src/app.py\n") == ["src/app.py"]


def test_touched_paths_of_a_headerless_patch_is_empty() -> None:
    assert touched_paths("@@ -1 +1 @@\n-old\n+new\n") == []


def test_touched_paths_of_an_empty_patch_is_empty() -> None:
    assert touched_paths("") == []
