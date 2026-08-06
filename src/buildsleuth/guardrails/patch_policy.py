"""Limits on what a generated patch is allowed to contain.

These exist because the model is not the thing deciding what lands. A patch
that is too large to review, touches the workflow that runs the checks, or
carries something that looks like a credential is refused here rather than
argued about in review.
"""

import re
from dataclasses import dataclass

from buildsleuth.models.taxonomy import FailureClass
from buildsleuth.pipeline.verify import touched_paths

MAX_FILES = 5
MAX_CHANGED_LINES = 200
WORKFLOW_PREFIX = ".github/workflows/"

# Deliberately narrow. This catches a credential pasted into a patch, not
# every string that happens to look random.
_SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][^'\"]{12,}['\"]"),
)


@dataclass(frozen=True)
class PolicyViolation:
    """Why a patch may not be opened as a pull request."""

    reason: str


def changed_line_count(patch: str) -> int:
    """Added and removed lines, ignoring the diff's own headers."""
    return sum(
        1
        for line in patch.splitlines()
        if (line.startswith(("+", "-")) and not line.startswith(("+++", "---")))
    )


def find_secret(patch: str) -> str | None:
    """Name the kind of credential a patch appears to contain, if any."""
    for pattern in _SECRET_PATTERNS:
        if pattern.search(patch):
            return pattern.pattern
    return None


def check_patch(patch: str, failure_class: FailureClass) -> PolicyViolation | None:
    """Return None when a patch may be proposed, else why it may not."""
    if not patch.strip():
        return PolicyViolation("the patch is empty")

    paths = touched_paths(patch)
    if len(paths) > MAX_FILES:
        return PolicyViolation(f"touches {len(paths)} files, more than the limit of {MAX_FILES}")

    changed = changed_line_count(patch)
    if changed > MAX_CHANGED_LINES:
        return PolicyViolation(
            f"changes {changed} lines, more than the limit of {MAX_CHANGED_LINES}."
            " A patch that large needs a person to write it."
        )

    if find_secret(patch) is not None:
        return PolicyViolation("contains something shaped like a credential")

    workflow_edits = [path for path in paths if path.startswith(WORKFLOW_PREFIX)]
    if workflow_edits and failure_class is not FailureClass.PIPELINE_CONFIG:
        return PolicyViolation(
            f"edits {workflow_edits[0]} for a {failure_class.value} failure."
            " Only a pipeline config failure justifies changing the checks themselves."
        )
    return None
