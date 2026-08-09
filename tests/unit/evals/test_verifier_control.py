"""Tests for the check that the verifier can tell a good patch from a bad one."""

import json
from pathlib import Path

from evals.fix_funnel import executable_cases, unmeasurable_cases
from evals.verifier_control import CORRUPTION, corrupt, reference_diff

from buildsleuth.dataset.loader import case_dir_for, load_cases, read_case_log

DATASET = Path("dataset")

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


class TestAgainstTheRealDataset:
    """Both entry points once passed dataset/cases where the dataset root was
    wanted, and every case failed to resolve on a runner. Nothing caught it
    because neither path was exercised against the real layout."""

    def test_every_executable_case_has_a_readable_reference_fix(self) -> None:
        cases = executable_cases(load_cases(DATASET))
        assert cases, "expected executable cases in the dataset"
        for case in cases:
            assert reference_diff(DATASET, case), f"{case.case_id} has no reference fix"

    def test_every_executable_case_has_a_readable_log(self) -> None:
        for case in executable_cases(load_cases(DATASET)):
            assert read_case_log(case_dir_for(DATASET, case), case).strip()

    def test_reference_fixes_are_ordinary_patches(self) -> None:
        # Absolute container paths in the header make the patch unapplicable
        # without knowing how many components to strip.
        for case in executable_cases(load_cases(DATASET)):
            diff = reference_diff(DATASET, case) or ""
            assert "--- a/" in diff, f"{case.case_id} keeps container paths"


class TestExclusion:
    def test_the_funnel_drops_cases_the_control_marked_unusable(self, tmp_path: Path) -> None:
        control = tmp_path / "control.json"
        control.write_text(
            json.dumps(
                {"cases": [{"case_id": "a", "usable": False}, {"case_id": "b", "usable": True}]}
            ),
            encoding="utf-8",
        )
        assert unmeasurable_cases(control) == {"a"}

    def test_a_missing_control_excludes_nothing(self, tmp_path: Path) -> None:
        # Without a control run the funnel still works, it just cannot say
        # which cases are sound.
        assert unmeasurable_cases(tmp_path / "absent.json") == set()

    def test_the_recorded_control_excludes_the_cases_it_found_broken(self) -> None:
        # The committed control run: five of thirteen reference fixes did not
        # pass, so those cases cannot measure a patch.
        assert len(unmeasurable_cases()) == 5
