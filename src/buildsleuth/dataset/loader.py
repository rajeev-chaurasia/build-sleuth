"""Read curated triage cases from the on-disk dataset.

A case directory is self-contained: case.json plus every file it references.
The loader enforces that, so a case can never silently lose the log it is
supposed to be scored on.
"""

from pathlib import Path

from pydantic import ValidationError

from buildsleuth.condense.clean import normalize_line_breaks
from buildsleuth.models.case import SMOKE_TAG, TriageCase

CASES_DIR_NAME = "cases"
CASE_FILE_NAME = "case.json"
MANIFEST_FILE_NAME = "manifest.json"

SMOKE_SUBSET = SMOKE_TAG
LOG_SEPARATOR = "\n"


class DatasetError(Exception):
    """A case is missing, malformed, or references a file that is not on disk."""


def referenced_files(case: TriageCase) -> list[str]:
    """Every input path of a case, relative to its case directory."""
    inputs = case.inputs
    optional = (inputs.diff_file, inputs.workflow_file, inputs.passing_run_log)
    return [*inputs.log_files, *(name for name in optional if name is not None)]


def read_normalized_text(path: Path) -> str:
    """Read a text file with LF line endings so reads match across platforms."""
    return normalize_line_breaks(path.read_text(encoding="utf-8", errors="replace"))


def load_case(case_dir: Path) -> TriageCase:
    """Load and validate one case, including that every file it references exists."""
    case_file = case_dir / CASE_FILE_NAME
    if not case_file.is_file():
        raise DatasetError(f"no {CASE_FILE_NAME} in {case_dir}")

    try:
        case = TriageCase.model_validate_json(case_file.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise DatasetError(f"case in {case_dir} failed validation: {exc}") from exc

    for name in referenced_files(case):
        _resolve(case_dir, case.case_id, name)
    return case


def load_cases(dataset_dir: Path, subset: str | None = None) -> list[TriageCase]:
    """Load every case under dataset_dir, sorted by case id. subset="smoke" filters."""
    cases = [load_case(path.parent) for path in _case_files(dataset_dir)]

    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            raise DatasetError(f"duplicate case id {case.case_id}")
        seen.add(case.case_id)

    if subset is not None:
        if subset != SMOKE_SUBSET:
            raise DatasetError(f"unknown subset {subset!r}")
        cases = [case for case in cases if case.is_smoke]
    return sorted(cases, key=lambda case: case.case_id)


def read_case_log(case_dir: Path, case: TriageCase) -> str:
    """Concatenate the cleaned logs the case points at, in the order listed."""
    parts = [
        read_normalized_text(_resolve(case_dir, case.case_id, name))
        for name in case.inputs.log_files
    ]
    return LOG_SEPARATOR.join(parts)


def read_case_diff(case_dir: Path, case: TriageCase) -> str | None:
    """Read the snapshotted diff, or None when the case has no diff."""
    name = case.inputs.diff_file
    if name is None:
        return None
    return read_normalized_text(_resolve(case_dir, case.case_id, name))


def case_dir_for(dataset_dir: Path, case: TriageCase) -> Path:
    """Locate the directory holding a case. The directory name is the case id."""
    for path in _case_files(dataset_dir):
        if path.parent.name == case.case_id:
            return path.parent
    raise DatasetError(f"no case directory named {case.case_id} under {dataset_dir}")


def _case_files(dataset_dir: Path) -> list[Path]:
    cases_dir = dataset_dir / CASES_DIR_NAME
    if not cases_dir.is_dir():
        raise DatasetError(f"no {CASES_DIR_NAME} directory under {dataset_dir}")
    return sorted(cases_dir.rglob(CASE_FILE_NAME))


def _resolve(case_dir: Path, case_id: str, name: str) -> Path:
    path = case_dir / name
    # A case must stay self-contained, so an input may not point outside its own directory.
    if not path.resolve().is_relative_to(case_dir.resolve()):
        raise DatasetError(f"case {case_id} references {name!r} outside its case directory")
    if not path.is_file():
        raise DatasetError(f"case {case_id} references missing file {name!r}, expected at {path}")
    return path
