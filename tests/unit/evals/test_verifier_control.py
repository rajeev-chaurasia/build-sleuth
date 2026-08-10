"""Tests for the check that the verifier can tell a good patch from a bad one."""

import json
from pathlib import Path

from evals.fix_funnel import executable_cases, unmeasurable_cases
from evals.verifier_control import CORRUPTION, corrupt, reference_diff

from buildsleuth.dataset.loader import case_dir_for, load_cases, read_case_log
from buildsleuth.pipeline.patch_repair import repair_patch

DATASET = Path("dataset")

DIFF = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,7 +1,7 @@\n-a\n+b\n"


class TestCorrupt:
    """The corruption must be one nothing downstream can undo. The first
    version damaged the hunk header, and patch_repair recomputes headers from
    the body, so the broken patch applied and nine good cases were reported
    as unable to measure anything."""

    def test_damages_a_line_the_file_must_match(self) -> None:
        assert CORRUPTION in corrupt(DIFF)

    def test_survives_a_repair_pass(self) -> None:
        # This is the property that matters. Recomputing headers must not
        # turn the corrupted patch back into a working one.
        assert CORRUPTION in repair_patch(corrupt(DIFF))

    def test_leaves_the_headers_alone(self) -> None:
        # Damaging the header is exactly what repair undoes.
        assert "@@ -1,7 +1,7 @@" in corrupt(DIFF)

    def test_keeps_the_line_marker(self) -> None:
        # Dropping it would make the patch malformed rather than unmatchable,
        # which is a different failure from the one being tested.
        corrupted = [line for line in corrupt(DIFF).splitlines() if CORRUPTION in line]
        assert corrupted[0][0] in " -"

    def test_does_not_damage_the_file_header(self) -> None:
        # "--- a/x.py" starts with a hyphen but is not a removal line.
        assert "--- a/x.py" in corrupt(DIFF)

    def test_ends_with_a_newline_so_git_accepts_it(self) -> None:
        # A patch missing its trailing newline is rejected for the wrong
        # reason, which would make this control pass by accident.
        assert corrupt(DIFF).endswith("\n")

    def test_a_diff_with_no_body_is_returned_terminated(self) -> None:
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

    def test_the_recorded_control_agrees_with_its_own_summary(self) -> None:
        # Checks the committed run against its own counts, so a file edited
        # by hand or written by a half-finished run does not silently change
        # which cases the funnel measures.
        record = json.loads(Path("results/verifier-control.json").read_text(encoding="utf-8"))
        assert len(unmeasurable_cases()) == record["n_checked"] - record["n_usable"]

    def test_every_excluded_case_reached_a_level_below_the_top(self) -> None:
        record = json.loads(Path("results/verifier-control.json").read_text(encoding="utf-8"))
        for case in record["cases"]:
            if not case["usable"]:
                # Either the reference fix fell short, or the corrupted one
                # passed as well. Both are unusable and both must say why.
                assert case["reason"]
