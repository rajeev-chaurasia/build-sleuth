"""Stage: check a proposed patch, cheapest test first.

The levels are ordered so a patch fails as early and as cheaply as possible.
A diff that will not even apply should never reach a container. Each level is
deterministic; nothing here asks a model whether the patch is good.
"""

import subprocess
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

GIT = "git"
APPLY_CHECK_TIMEOUT_SECONDS = 30
EMPTY_PATCH_REASON = "the proposal contains no patch"


class VerificationLevel(IntEnum):
    """How far a patch got. Higher is better, and the order is the cost order."""

    NOTHING = 0
    APPLIES = 1
    LINTS = 2
    FAILING_TEST_PASSES = 3
    NOTHING_ELSE_BROKE = 4


@dataclass(frozen=True)
class VerificationResult:
    """The highest level a patch reached, and what stopped it there."""

    level: VerificationLevel
    detail: str
    stdout_tail: str = ""

    @property
    def applied(self) -> bool:
        return self.level >= VerificationLevel.APPLIES

    @property
    def fixed_the_failure(self) -> bool:
        return self.level >= VerificationLevel.FAILING_TEST_PASSES


def check_applies(
    patch: str, repo_dir: Path, timeout: int = APPLY_CHECK_TIMEOUT_SECONDS
) -> VerificationResult:
    """Ask git whether the patch would apply, without changing anything.

    This is the cheapest possible check and it rejects most bad patches: a
    model that invented line numbers or paths fails here in milliseconds.
    """
    if not patch.strip():
        return VerificationResult(VerificationLevel.NOTHING, EMPTY_PATCH_REASON)

    try:
        completed = subprocess.run(
            [GIT, "apply", "--check", "--verbose", "-"],
            cwd=repo_dir,
            # Bytes, not text. On Windows text mode rewrites every newline on
            # the way to stdin, and git then cannot match a single line of a
            # patch it was handed itself.
            input=patch.encode("utf-8"),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return VerificationResult(
            VerificationLevel.NOTHING, f"could not run git apply: {type(error).__name__}"
        )

    if completed.returncode == 0:
        return VerificationResult(VerificationLevel.APPLIES, "patch applies cleanly")
    stderr = _decode(completed.stderr) or _decode(completed.stdout)
    return VerificationResult(
        VerificationLevel.NOTHING, "patch does not apply", stdout_tail=stderr[-800:]
    )


def _decode(raw: bytes | None) -> str:
    return raw.decode("utf-8", errors="replace") if raw else ""


def touched_paths(patch: str) -> list[str]:
    """Paths a unified diff edits, taken from its own headers.

    Read from the patch rather than trusting what the model said it touched,
    since the guardrails downstream act on this.
    """
    paths: list[str] = []
    for line in patch.splitlines():
        if not line.startswith("+++ "):
            continue
        target = line[4:].strip()
        if target == "/dev/null":
            continue
        cleaned = target.split("\t")[0]
        if cleaned.startswith("b/"):
            cleaned = cleaned[2:]
        if cleaned not in paths:
            paths.append(cleaned)
    return paths
