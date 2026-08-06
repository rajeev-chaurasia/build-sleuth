"""Tests for the dataset content hash and the manifest round trip."""

from collections.abc import Callable
from pathlib import Path

import pytest

from buildsleuth.dataset.loader import DatasetError, load_cases
from buildsleuth.dataset.manifest import (
    HASH_LENGTH,
    build_manifest,
    dataset_hash,
    read_manifest,
    write_manifest,
)
from buildsleuth.models.case import CaseSource
from buildsleuth.models.taxonomy import FailureClass

CaseWriter = Callable[..., Path]
LOG_NAME = "logs/failed_job.txt"


def test_hash_is_stable_across_calls(tmp_path: Path, write_case: CaseWriter) -> None:
    write_case("sy-0001")
    write_case("sy-0002")
    cases = load_cases(tmp_path)
    first = dataset_hash(cases, tmp_path)
    assert first == dataset_hash(load_cases(tmp_path), tmp_path)
    assert len(first) == HASH_LENGTH


def test_hash_ignores_case_order(tmp_path: Path, write_case: CaseWriter) -> None:
    write_case("sy-0001")
    write_case("sy-0002")
    cases = load_cases(tmp_path)
    assert dataset_hash(cases, tmp_path) == dataset_hash(list(reversed(cases)), tmp_path)


def test_hash_changes_when_a_referenced_file_changes(
    tmp_path: Path, write_case: CaseWriter
) -> None:
    case_dir = write_case("sy-0001", log_text="original failure\n")
    before = dataset_hash(load_cases(tmp_path), tmp_path)
    (case_dir / LOG_NAME).write_text("different failure\n", encoding="utf-8", newline="\n")
    assert dataset_hash(load_cases(tmp_path), tmp_path) != before


def test_hash_ignores_line_ending_differences(tmp_path: Path, write_case: CaseWriter) -> None:
    case_dir = write_case("sy-0001", log_text="first\nsecond\n")
    before = dataset_hash(load_cases(tmp_path), tmp_path)
    (case_dir / LOG_NAME).write_bytes(b"first\r\nsecond\r\n")
    assert dataset_hash(load_cases(tmp_path), tmp_path) == before


def test_hash_changes_when_a_label_changes(tmp_path: Path, write_case: CaseWriter) -> None:
    write_case("sy-0001")
    cases = load_cases(tmp_path)
    before = dataset_hash(cases, tmp_path)
    cases[0].ground_truth.related_to_diff = False
    assert dataset_hash(cases, tmp_path) != before


def test_hash_changes_when_a_case_is_added(tmp_path: Path, write_case: CaseWriter) -> None:
    write_case("sy-0001")
    before = dataset_hash(load_cases(tmp_path), tmp_path)
    write_case("sy-0002")
    assert dataset_hash(load_cases(tmp_path), tmp_path) != before


def test_build_manifest_summarizes_every_case(tmp_path: Path, write_case: CaseWriter) -> None:
    write_case("sy-0001", tags=["docs"])
    write_case("sy-0002")
    manifest = build_manifest(tmp_path)
    assert manifest.case_count == 2
    assert [entry.case_id for entry in manifest.cases] == ["sy-0001", "sy-0002"]
    assert manifest.cases[0].failure_class is FailureClass.CODE_CHANGE
    assert manifest.cases[0].source is CaseSource.SYNTHETIC
    assert manifest.cases[0].tags == ["docs"]


def test_manifest_round_trip(tmp_path: Path, write_case: CaseWriter) -> None:
    write_case("sy-0001")
    manifest = build_manifest(tmp_path)
    path = write_manifest(tmp_path, manifest)
    assert path.read_bytes().endswith(b"}\n")
    assert b"\r\n" not in path.read_bytes()
    assert read_manifest(tmp_path) == manifest


def test_read_manifest_raises_when_absent(tmp_path: Path) -> None:
    with pytest.raises(DatasetError, match=r"no manifest\.json"):
        read_manifest(tmp_path)


def test_read_manifest_raises_on_invalid_json(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text('{"case_count": 1}', encoding="utf-8")
    with pytest.raises(DatasetError, match="failed validation"):
        read_manifest(tmp_path)
