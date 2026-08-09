"""Tests for assembling a fix funnel out of per-case runs.

Each case runs on its own machine, so the funnel is stitched together from
files rather than accumulated in one process. A miscount here reports a fix
rate nobody measured, which is worse than reporting nothing.
"""

import json
from pathlib import Path

from evals.fix_funnel import (
    IMAGE_TAG_PREFIX,
    PatchFormat,
    aggregate,
    edited_patch,
    executable_cases,
    image_tag,
    read_attempts,
    read_files_for,
)
from evals.verifier_control import summarize

from buildsleuth.dataset.loader import load_cases
from buildsleuth.models.triage import AnchoredEdit
from buildsleuth.pipeline.anchored_edits import patch_from_edits
from buildsleuth.pipeline.verify import VerificationLevel
from buildsleuth.sandbox.bugswarm_runner import FILE_MARKER, parse_files, read_script

DATASET = Path("dataset")


def _attempt_file(directory: Path, name: str, attempts: list[dict[str, object]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps({"attempts": attempts}), encoding="utf-8")


class TestExecutableCases:
    def test_selects_only_cases_carrying_an_image(self) -> None:
        cases = executable_cases(load_cases(DATASET))
        assert cases
        for case in cases:
            assert case.verification is not None
            assert case.verification.docker_image

    def test_leaves_the_mined_cases_out(self) -> None:
        every = load_cases(DATASET)
        assert len(executable_cases(every)) < len(every)

    def test_strips_the_registry_prefix_from_the_tag(self) -> None:
        case = executable_cases(load_cases(DATASET))[0]
        assert not image_tag(case).startswith(IMAGE_TAG_PREFIX)
        assert image_tag(case)


class TestReadAttempts:
    def test_reads_attempts_from_separate_files(self, tmp_path: Path) -> None:
        _attempt_file(
            tmp_path / "a", "one.json", [{"case_id": "x", "attempted": True, "level": "APPLIES"}]
        )
        _attempt_file(
            tmp_path / "b",
            "two.json",
            [{"case_id": "y", "attempted": True, "level": "NOTHING_ELSE_BROKE"}],
        )
        attempts = read_attempts(tmp_path)
        assert {a.case_id for a in attempts} == {"x", "y"}

    def test_a_skipped_case_reads_back_as_not_attempted(self, tmp_path: Path) -> None:
        _attempt_file(
            tmp_path,
            "s.json",
            [{"case_id": "z", "attempted": False, "level": None, "skip_reason": "declined"}],
        )
        attempt = read_attempts(tmp_path)[0]
        assert attempt.attempted is False
        assert attempt.skip_reason == "declined"
        assert attempt.level is VerificationLevel.NOTHING

    def test_ignores_files_that_are_not_attempt_records(self, tmp_path: Path) -> None:
        # Control files land in the same directory when both run.
        (tmp_path / "control.json").write_text(
            json.dumps({"cases": [{"case_id": "q", "usable": True}], "n_usable": 1}),
            encoding="utf-8",
        )
        assert read_attempts(tmp_path) == []


class TestAggregate:
    def test_counts_each_rung_cumulatively(self, tmp_path: Path) -> None:
        _attempt_file(
            tmp_path,
            "all.json",
            [
                {"case_id": "a", "attempted": True, "level": "NOTHING"},
                {"case_id": "b", "attempted": True, "level": "APPLIES"},
                {"case_id": "c", "attempted": True, "level": "NOTHING_ELSE_BROKE"},
            ],
        )
        out = tmp_path / "out.json"
        assert aggregate(tmp_path, out) == 0

        report = json.loads(out.read_text(encoding="utf-8"))["report"]
        assert report["n_attempted"] == 3
        # A patch reaching the top rung also cleared every rung below it.
        assert report["applies"] == 2
        assert report["nothing_else_broke"] == 1

    def test_reports_nothing_rather_than_zero_when_there_are_no_files(self, tmp_path: Path) -> None:
        # An empty funnel must not read as a measured rate of zero.
        assert aggregate(tmp_path, tmp_path / "out.json") == 1
        assert not (tmp_path / "out.json").exists()

    def test_orders_attempts_by_case_id(self, tmp_path: Path) -> None:
        _attempt_file(
            tmp_path,
            "x.json",
            [
                {"case_id": "bs-0021", "attempted": True, "level": "NOTHING"},
                {"case_id": "bs-0010", "attempted": True, "level": "NOTHING"},
            ],
        )
        out = tmp_path / "out.json"
        aggregate(tmp_path, out)
        ids = [a["case_id"] for a in json.loads(out.read_text(encoding="utf-8"))["attempts"]]
        assert ids == sorted(ids)


class TestControlSummary:
    def test_counts_the_usable_cases(self, tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
        (tmp_path / "c.json").write_text(
            json.dumps(
                {
                    "n_usable": 1,
                    "n_checked": 2,
                    "cases": [
                        {"case_id": "a", "usable": True},
                        {"case_id": "b", "usable": False, "reason": "reference fix failed"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        assert summarize(tmp_path) == 0
        out = capsys.readouterr().out
        assert "1 of 2 cases can measure a fix" in out
        assert "reference fix failed" in out

    def test_says_so_when_there_is_nothing_to_summarize(self, tmp_path: Path) -> None:
        assert summarize(tmp_path) == 0


class TestFilesReachTheModel:
    """The funnel once passed an empty mapping, so the model was asked to
    write a unified diff for a file it had never seen, and its context lines
    could only match by luck. Fetching the upstream copy at the same commit
    was no better: these images are patched for reproducibility, so upstream
    and artifact disagree."""

    def test_no_paths_means_no_container(self) -> None:
        case = executable_cases(load_cases(DATASET))[0]
        assert read_files_for(case, []) == {}

    def test_files_are_split_on_the_marker(self) -> None:
        output = f"{FILE_MARKER}a.py\nline one\n{FILE_MARKER}b.py\nline two\n"
        assert parse_files(output) == {"a.py": "line one\n", "b.py": "line two\n"}

    def test_a_missing_file_is_left_out_rather_than_reported_empty(self) -> None:
        # cat prints nothing for a path that is not there, and an empty
        # string would read as a file whose contents are blank.
        assert parse_files(f"{FILE_MARKER}gone.py\n") == {}

    def test_reading_preserves_line_endings(self) -> None:
        # A CRLF file handed to the model as LF produces a patch that cannot
        # apply to it.
        assert parse_files(f"{FILE_MARKER}a.py\r\nx = 1\r\n")["a.py"] == "x = 1\r\n"

    def test_the_read_happens_inside_the_checkout(self) -> None:
        assert 'cd "$FAILED/$PROJ"' in read_script(["a.py"])


class TestAnchoredEditsReachTheVerifier:
    """Four of the nine attempts in the last funnel were rejected because the
    model retyped context that did not match. Asking for edits instead moves
    that failure from the container to a reported rejection here."""

    SOURCE = "def go():\n    return 1\n"
    EDIT = AnchoredEdit(path="a.py", find="    return 1\n", replace="    return 2\n")

    def test_edits_are_the_default_format(self) -> None:
        assert PatchFormat.EDITS is PatchFormat("edits")

    def test_a_placed_edit_becomes_the_patch_to_verify(self) -> None:
        built = patch_from_edits([self.EDIT], {"a.py": self.SOURCE})
        assert edited_patch(built) == built.patch
        assert "+    return 2" in edited_patch(built)

    def test_an_edit_that_did_not_land_verifies_nothing(self) -> None:
        # Running half a proposal reports a build failure caused by the half
        # that is missing, which is worse than reporting that it missed.
        missing = AnchoredEdit(path="a.py", find="    return 99\n", replace="    return 3\n")
        built = patch_from_edits([self.EDIT, missing], {"a.py": self.SOURCE})
        assert edited_patch(built) == ""
        assert "does not occur" in built.report

    def test_the_notes_survive_a_round_trip_through_the_attempt_file(self, tmp_path: Path) -> None:
        _attempt_file(
            tmp_path,
            "n.json",
            [
                {
                    "case_id": "bs-0010",
                    "attempted": True,
                    "level": "APPLIES",
                    "edit_notes": "placed 1 of 1 edits; a.py matched exactly",
                }
            ],
        )
        assert "matched exactly" in read_attempts(tmp_path)[0].edit_notes

    def test_an_attempt_file_written_before_the_notes_existed_still_reads(
        self, tmp_path: Path
    ) -> None:
        _attempt_file(tmp_path, "old.json", [{"case_id": "bs-0010", "attempted": True}])
        assert read_attempts(tmp_path)[0].edit_notes == ""
