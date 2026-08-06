"""A model's diff is usually correct and almost never perfectly formatted.

Both cases here were found by running the real pipeline against a real
failure: the patch said exactly the right thing and git refused it.
"""

import subprocess
from pathlib import Path

import pytest

from buildsleuth.pipeline.verify import VerificationLevel, check_applies, normalize_patch

ORIGINAL = 'import re\n\nPATTERN = re.compile(r"^x")\n'
PATCH_BODY = (
    "--- a/app.py\n"
    "+++ b/app.py\n"
    "@@ -1,3 +1,3 @@\n"
    " import re\n"
    " \n"
    '-PATTERN = re.compile(r"^x")\n'
    '+PATTERN = re.compile(r"^x", re.MULTILINE)'
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repository, since only git can say whether a patch applies."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".git" / "config").write_text(
        "[core]\n\tautocrlf = false\n", encoding="utf-8", newline="\n"
    )
    (tmp_path / "app.py").write_text(ORIGINAL, encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


def test_a_patch_without_a_trailing_newline_still_applies(repo: Path) -> None:
    """git calls this corrupt, which reads as a wrong patch rather than a slip."""
    assert not PATCH_BODY.endswith("\n")
    assert check_applies(PATCH_BODY, repo).level is VerificationLevel.APPLIES


def test_a_patch_with_windows_line_endings_still_applies(repo: Path) -> None:
    assert check_applies(PATCH_BODY.replace("\n", "\r\n"), repo).level is VerificationLevel.APPLIES


def test_normalizing_does_not_change_what_the_patch_says() -> None:
    normalized = normalize_patch(PATCH_BODY)
    assert normalized.rstrip("\n") == PATCH_BODY.rstrip("\n")
    assert normalized.endswith("\n")


def test_normalizing_leaves_an_already_clean_patch_alone() -> None:
    clean = PATCH_BODY + "\n"
    assert normalize_patch(clean) == clean


def test_an_empty_patch_is_left_empty_rather_than_given_a_newline() -> None:
    assert normalize_patch("") == ""
    assert normalize_patch("   ") == "   "


def test_a_genuinely_wrong_patch_is_still_refused(repo: Path) -> None:
    """Normalizing formatting must not start accepting patches that do not fit."""
    wrong = PATCH_BODY.replace("import re", "import os")
    assert check_applies(wrong, repo).level is VerificationLevel.NOTHING
