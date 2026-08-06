"""Tests for loading and validating cases off disk."""

from collections.abc import Callable
from pathlib import Path

import pytest

from buildsleuth.dataset.loader import (
    SMOKE_SUBSET,
    DatasetError,
    case_dir_for,
    load_case,
    load_cases,
    read_case_diff,
    read_case_log,
)
from buildsleuth.models.case import SMOKE_TAG

CaseWriter = Callable[..., Path]
LOG_NAME = "logs/failed_job.txt"


def test_load_case_reads_inputs_and_ground_truth(write_case: CaseWriter) -> None:
    case = load_case(write_case("sy-0001"))
    assert case.case_id == "sy-0001"
    assert case.inputs.log_files == [LOG_NAME]
    assert case.ground_truth.subcategory == "test_assertion"


def test_missing_case_file_raises(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match=r"no case\.json"):
        load_case(tmp_path)


def test_missing_referenced_log_raises_naming_case_and_file(write_case: CaseWriter) -> None:
    case_dir = write_case("sy-0001", write_log=False)
    with pytest.raises(DatasetError) as exc:
        load_case(case_dir)
    assert "sy-0001" in str(exc.value)
    assert LOG_NAME in str(exc.value)


def test_missing_referenced_diff_raises(write_case: CaseWriter) -> None:
    case_dir = write_case("sy-0001", diff_file="diff.patch")
    with pytest.raises(DatasetError, match=r"diff\.patch"):
        load_case(case_dir)


def test_input_pointing_outside_the_case_directory_raises(write_case: CaseWriter) -> None:
    case_dir = write_case("sy-0001", log_files=["../escape.txt"], write_log=False)
    (case_dir.parent / "escape.txt").write_text("boom\n", encoding="utf-8")
    with pytest.raises(DatasetError, match="outside its case directory"):
        load_case(case_dir)


def test_invalid_case_json_raises(write_case: CaseWriter) -> None:
    case_dir = write_case("sy-0001")
    (case_dir / "case.json").write_text('{"case_id": "sy-0001"}', encoding="utf-8")
    with pytest.raises(DatasetError, match="failed validation"):
        load_case(case_dir)


def test_load_cases_is_sorted_by_case_id(tmp_path: Path, write_case: CaseWriter) -> None:
    write_case("sy-0003", group="synthetic")
    write_case("gm-0002", group="gha-mined")
    write_case("gm-0001", group="gha-mined")
    assert [case.case_id for case in load_cases(tmp_path)] == ["gm-0001", "gm-0002", "sy-0003"]


def test_smoke_subset_filters_to_tagged_cases(tmp_path: Path, write_case: CaseWriter) -> None:
    write_case("sy-0001", tags=[SMOKE_TAG])
    write_case("sy-0002", tags=["slow"])
    assert len(load_cases(tmp_path)) == 2
    assert [case.case_id for case in load_cases(tmp_path, subset=SMOKE_SUBSET)] == ["sy-0001"]


def test_unknown_subset_raises(tmp_path: Path, write_case: CaseWriter) -> None:
    write_case("sy-0001")
    with pytest.raises(DatasetError, match="unknown subset"):
        load_cases(tmp_path, subset="nope")


def test_duplicate_case_ids_raise(tmp_path: Path, write_case: CaseWriter) -> None:
    write_case("sy-0001", group="synthetic")
    write_case("sy-0001", group="gha-mined")
    with pytest.raises(DatasetError, match="duplicate case id"):
        load_cases(tmp_path)


def test_missing_cases_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match="no cases directory"):
        load_cases(tmp_path)


def test_read_case_log_concatenates_in_listed_order(write_case: CaseWriter) -> None:
    case_dir = write_case("sy-0001", log_files=["logs/a.txt", "logs/b.txt"], log_text="one\n")
    (case_dir / "logs" / "b.txt").write_text("two\n", encoding="utf-8", newline="\n")
    assert read_case_log(case_dir, load_case(case_dir)) == "one\n\ntwo\n"


def test_read_case_log_normalizes_crlf(write_case: CaseWriter) -> None:
    case_dir = write_case("sy-0001")
    (case_dir / LOG_NAME).write_bytes(b"first\r\nsecond\r\n")
    assert read_case_log(case_dir, load_case(case_dir)) == "first\nsecond\n"


def test_read_case_diff_returns_none_without_a_diff(write_case: CaseWriter) -> None:
    case_dir = write_case("sy-0001")
    assert read_case_diff(case_dir, load_case(case_dir)) is None


def test_read_case_diff_returns_the_patch(write_case: CaseWriter) -> None:
    case_dir = write_case("sy-0001", diff_file="diff.patch", diff_text="--- a\n+++ b\n")
    assert read_case_diff(case_dir, load_case(case_dir)) == "--- a\n+++ b\n"


def test_case_dir_for_locates_the_directory(tmp_path: Path, write_case: CaseWriter) -> None:
    expected = write_case("gm-0001", group="gha-mined")
    write_case("sy-0002", group="synthetic")
    assert case_dir_for(tmp_path, load_cases(tmp_path)[0]) == expected


def test_case_dir_for_raises_when_absent(tmp_path: Path, write_case: CaseWriter) -> None:
    write_case("sy-0001")
    case = load_cases(tmp_path)[0]
    other = tmp_path / "elsewhere"
    (other / "cases").mkdir(parents=True)
    with pytest.raises(DatasetError, match="no case directory named sy-0001"):
        case_dir_for(other, case)
