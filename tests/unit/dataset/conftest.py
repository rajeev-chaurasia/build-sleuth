"""Tiny synthetic case directories, so dataset tests never depend on the real dataset."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from buildsleuth.dataset.loader import CASE_FILE_NAME, CASES_DIR_NAME
from buildsleuth.models.case import SMOKE_TAG

CaseWriter = Callable[..., Path]

LOG_NAME = "logs/failed_job.txt"
DEFAULT_LOG = "FAILED tests/test_thing.py::test_thing\n"


def _case_payload(case_id: str, tags: list[str], log_files: list[str]) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "title": f"case {case_id}",
        "inputs": {
            "repo": "acme/widget",
            "run_id": 1,
            "head_sha": "0" * 40,
            "failed_job_name": "tests",
            "log_files": log_files,
        },
        "ground_truth": {
            "failure_class": "code_change",
            "subcategory": "test_assertion",
            "related_to_diff": True,
        },
        "provenance": {
            "source": "synthetic",
            "labeling_method": "constructed",
            "verified_by_human": SMOKE_TAG in tags,
            "snapshot_date": "2026-01-01",
        },
        "tags": tags,
    }


def _write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def write_case(tmp_path: Path) -> CaseWriter:
    """Create dataset/cases/<group>/<case_id>/ with a case.json and its log."""

    def _write(
        case_id: str,
        *,
        group: str = "synthetic",
        tags: list[str] | None = None,
        log_files: list[str] | None = None,
        log_text: str = DEFAULT_LOG,
        write_log: bool = True,
        diff_file: str | None = None,
        diff_text: str | None = None,
    ) -> Path:
        case_dir = tmp_path / CASES_DIR_NAME / group / case_id
        case_dir.mkdir(parents=True)
        payload = _case_payload(case_id, tags or [], log_files or [LOG_NAME])
        if diff_file is not None:
            payload["inputs"]["diff_file"] = diff_file
        (case_dir / CASE_FILE_NAME).write_text(json.dumps(payload), encoding="utf-8")
        if write_log:
            for name in payload["inputs"]["log_files"]:
                _write_file(case_dir / name, log_text)
        if diff_file is not None and diff_text is not None:
            _write_file(case_dir / diff_file, diff_text)
        return case_dir

    return _write
