"""Helpers for reading GitHub Actions run-logs zip archives.

Zip layout: root-level files named "{N}_{jobName}.txt" hold the combined log
of one job, and one folder per job holds "{stepNumber}_{stepName}.txt" files.
GitHub turns "/" and ":" in job names into spaces and truncates long names,
so lookups match by normalized prefix and prefer the longest candidate.
"""

import io
import re
import zipfile
from collections.abc import Iterable

_ROOT_LOG_RE = re.compile(r"\d+_(?P<job>.+)\.txt")
_LOG_SUFFIX = ".txt"
_SANITIZED_CHARS = ("/", ":")
# GitHub truncates long job names in zip entries. Only names at least this long
# are treated as possibly-truncated; shorter names must match exactly, so a
# lookup for "builder" never resolves to an unrelated "build" entry.
_TRUNCATION_THRESHOLD = 80


def extract_job_log(zip_bytes: bytes, job_name: str) -> str | None:
    """Return a job's combined log from a run-logs zip, or None when absent."""
    target = _normalize(job_name)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        stems: dict[str, str] = {}
        for name in archive.namelist():
            if "/" in name:
                continue
            match = _ROOT_LOG_RE.fullmatch(name)
            if match is not None:
                stems[match["job"]] = name
        stem = _best_match(stems, target)
        if stem is None:
            return None
        return _read_text(archive, stems[stem])


def extract_step_log(zip_bytes: bytes, job_name: str, step_number: int) -> str | None:
    """Return one step's log from a run-logs zip, or None when absent."""
    target = _normalize(job_name)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        folder = _best_match(_job_folders(archive), target)
        if folder is None:
            return None
        prefix = f"{folder}/{step_number}_"
        for name in archive.namelist():
            if name.startswith(prefix) and name.endswith(_LOG_SUFFIX):
                return _read_text(archive, name)
    return None


def _normalize(job_name: str) -> str:
    """Apply the character substitutions GitHub uses in zip entry names."""
    normalized = job_name
    for char in _SANITIZED_CHARS:
        normalized = normalized.replace(char, " ")
    return normalized


def _best_match(candidates: Iterable[str], target: str) -> str | None:
    """Pick the candidate equal to target, else the longest truncated-prefix match."""
    best: str | None = None
    for candidate in candidates:
        if candidate == target:
            return candidate
        looks_truncated = len(candidate) >= _TRUNCATION_THRESHOLD
        if not candidate or not looks_truncated or not target.startswith(candidate):
            continue
        if best is None or len(candidate) > len(best):
            best = candidate
    return best


def _job_folders(archive: zipfile.ZipFile) -> set[str]:
    folders: set[str] = set()
    for name in archive.namelist():
        head, sep, _ = name.partition("/")
        if sep:
            folders.add(head)
    return folders


def _read_text(archive: zipfile.ZipFile, name: str) -> str:
    return archive.read(name).decode("utf-8", errors="replace")
