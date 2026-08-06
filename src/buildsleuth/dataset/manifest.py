"""Content hash and manifest for the curated dataset.

The hash pins both the labels and the snapshotted files, so a scorecard can
name the exact dataset it was measured on. It is stable across runs and
platforms: cases are sorted by id, JSON keys are sorted, text is read with LF
line endings, and nothing time-dependent is folded in.
"""

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ValidationError

from buildsleuth.dataset.loader import (
    MANIFEST_FILE_NAME,
    DatasetError,
    case_dir_for,
    load_cases,
    read_normalized_text,
    referenced_files,
)
from buildsleuth.models.case import CaseSource, TriageCase
from buildsleuth.models.taxonomy import FailureClass

MANIFEST_SCHEMA_VERSION = 1
HASH_LENGTH = 12

# Fed between fields so that concatenation can never make two different
# datasets hash the same.
_FIELD_SEPARATOR = b"\x00"


class ManifestCase(BaseModel):
    case_id: str
    failure_class: FailureClass
    source: CaseSource
    tags: list[str] = []


class Manifest(BaseModel):
    schema_version: int = MANIFEST_SCHEMA_VERSION
    dataset_hash: str
    case_count: int
    cases: list[ManifestCase]


def dataset_hash(cases: list[TriageCase], dataset_dir: Path | None = None) -> str:
    """Stable short digest of the case labels, plus their files when dataset_dir is given."""
    digest = hashlib.sha256()
    for case in sorted(cases, key=lambda case: case.case_id):
        digest.update(_canonical_json(case).encode("utf-8"))
        digest.update(_FIELD_SEPARATOR)
        if dataset_dir is None:
            continue
        case_dir = case_dir_for(dataset_dir, case)
        for name in sorted(referenced_files(case)):
            digest.update(name.encode("utf-8"))
            digest.update(_file_digest(case_dir / name).encode("ascii"))
            digest.update(_FIELD_SEPARATOR)
    return digest.hexdigest()[:HASH_LENGTH]


def build_manifest(dataset_dir: Path) -> Manifest:
    """Load every case and describe the dataset as it currently sits on disk."""
    cases = load_cases(dataset_dir)
    return Manifest(
        dataset_hash=dataset_hash(cases, dataset_dir),
        case_count=len(cases),
        cases=[
            ManifestCase(
                case_id=case.case_id,
                failure_class=case.ground_truth.failure_class,
                source=case.provenance.source,
                tags=case.tags,
            )
            for case in cases
        ],
    )


def write_manifest(dataset_dir: Path, manifest: Manifest) -> Path:
    """Write manifest.json with LF endings so the file is identical on every platform."""
    path = dataset_dir / MANIFEST_FILE_NAME
    path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def read_manifest(dataset_dir: Path) -> Manifest:
    """Read the manifest committed alongside the dataset."""
    path = dataset_dir / MANIFEST_FILE_NAME
    if not path.is_file():
        raise DatasetError(f"no {MANIFEST_FILE_NAME} in {dataset_dir}")
    try:
        return Manifest.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise DatasetError(f"{path} failed validation: {exc}") from exc


def _canonical_json(case: TriageCase) -> str:
    return json.dumps(
        case.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _file_digest(path: Path) -> str:
    return hashlib.sha256(read_normalized_text(path).encode("utf-8")).hexdigest()
