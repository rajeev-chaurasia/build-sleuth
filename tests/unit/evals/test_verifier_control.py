"""Tests for the check that the verifier can tell a good patch from a bad one."""

from evals.verifier_control import CORRUPTION, corrupt

DIFF = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,7 +1,7 @@\n-a\n+b\n"


class TestCorrupt:
    def test_damages_the_hunk_header(self) -> None:
        # The same failure the fix stage produced on the first executed case:
        # a hunk header claiming more lines than the hunk supplies.
        assert CORRUPTION in corrupt(DIFF)
        assert "@@ -1,7 +1,7 @@" not in corrupt(DIFF)

    def test_leaves_the_patch_parseable(self) -> None:
        broken = corrupt(DIFF)
        assert broken.startswith("diff --git")
        assert "--- a/x.py" in broken
        assert "+++ b/x.py" in broken

    def test_only_damages_the_first_hunk(self) -> None:
        two_hunks = DIFF + "@@ -20,3 +20,3 @@\n-c\n+d\n"
        assert corrupt(two_hunks).count("@@ -20,3 +20,3 @@") == 1

    def test_ends_with_a_newline_so_git_accepts_it(self) -> None:
        # A patch missing its trailing newline is rejected as corrupt for the
        # wrong reason, which would make this control pass by accident.
        assert corrupt(DIFF).endswith("\n")

    def test_a_diff_with_no_hunks_is_returned_unchanged_but_terminated(self) -> None:
        assert corrupt("diff --git a/x b/x") == "diff --git a/x b/x\n"
