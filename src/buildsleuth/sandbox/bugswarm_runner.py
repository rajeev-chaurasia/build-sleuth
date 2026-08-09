"""Run a proposed patch against the build that originally failed.

Every other verification path clones a repository and runs a test command
somebody wrote by hand. A BugSwarm image already contains the checkout at the
failing commit and the script the original job ran, so the patch is judged by
rerunning that job rather than by a reproduction somebody guessed at.

That gives the top rung of the hierarchy for free. The script reruns the whole
job, not one test, so a pass means the failure stopped and nothing else in the
suite broke. There is no cheaper rung to stop at and no judgement call left.
"""

import base64
import shlex
import subprocess
from dataclasses import dataclass

from buildsleuth.dataset.bugswarm import (
    CONTAINER_CPUS,
    CONTAINER_MEMORY,
    DOCKER,
    IMAGE_PREFIX,
    RUN_FAILED,
    checkout_resolution_lines,
)
from buildsleuth.pipeline.verify import VerificationLevel, VerificationResult, normalize_patch

BUILD_ROOT = "/home/travis/build"
PATCH_PATH = "/tmp/buildsleuth.patch"
DEFAULT_TIMEOUT_SECONDS = 1800
OUTPUT_TAIL_CHARS = 800
APPLY_MARKER = "=====BUILDSLEUTH_APPLIED====="


@dataclass(frozen=True)
class ReproductionResult:
    """What happened when the patched build was rerun."""

    applied: bool
    build_passed: bool
    output: str


def verification_script(patch: str, repo_name: str) -> str:
    """Apply the patch to the failing checkout and rerun the original job.

    The patch travels base64 encoded inside the script. Passing it on stdin
    competes with whatever the build script reads, and quoting a diff into a
    shell command corrupts it in ways that look like the model's fault.
    """
    encoded = base64.b64encode(normalize_patch(patch).encode("utf-8")).decode("ascii")
    return "\n".join(
        [
            "set +e",
            'LOG=$(find / -maxdepth 6 -name "*-orig.log" 2>/dev/null | head -1)',
            'ROOT=$(dirname "$LOG" 2>/dev/null)',
            f'[ -d "$ROOT/failed" ] || ROOT={BUILD_ROOT}',
            'FAILED="$ROOT/failed"',
            *checkout_resolution_lines(repo_name),
            f"echo {encoded} | base64 -d > {PATCH_PATH}",
            'cd "$FAILED/$PROJ" || exit 90',
            # git apply is stricter and reports better; patch is the fallback
            # for a checkout that is not a git repository.
            f"git apply --whitespace=nowarn {PATCH_PATH} 2>&1"
            f" || patch -p1 --batch --forward < {PATCH_PATH} 2>&1",
            "APPLIED=$?",
            f'echo "{APPLY_MARKER}$APPLIED"',
            '[ "$APPLIED" -eq 0 ] || exit 91',
            f"bash {shlex.quote(RUN_FAILED)} 2>&1",
        ]
    )


def parse_result(returncode: int, output: str) -> ReproductionResult:
    """Read the marker rather than trusting the exit code alone.

    The reproduction script can exit non-zero for reasons that have nothing to
    do with the patch, and counting those as a failed fix would understate the
    model. The marker says whether the patch itself went in.
    """
    applied = False
    for line in output.splitlines():
        if line.startswith(APPLY_MARKER):
            applied = line[len(APPLY_MARKER) :].strip() == "0"
    return ReproductionResult(
        applied=applied,
        build_passed=applied and returncode == 0,
        output=output,
    )


def to_verification(result: ReproductionResult) -> VerificationResult:
    """Map a rerun onto the hierarchy the rest of the eval reports."""
    tail = result.output[-OUTPUT_TAIL_CHARS:]
    if not result.applied:
        return VerificationResult(VerificationLevel.NOTHING, "patch does not apply", tail)
    if not result.build_passed:
        # APPLIES rather than LINTS: this path reruns the job and never lints,
        # so claiming the patch linted would report a check that never ran.
        return VerificationResult(
            VerificationLevel.APPLIES, "patch applies but the build still fails", tail
        )
    # The script reruns the whole job, so a pass covers the rest of the suite.
    return VerificationResult(
        VerificationLevel.NOTHING_ELSE_BROKE,
        "the original job passes with the patch applied",
        tail,
    )


def verify_in_image(
    patch: str, tag: str, repo_name: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
) -> VerificationResult:
    """Apply a patch inside the artifact image and rerun the failing build."""
    completed = subprocess.run(
        [
            DOCKER,
            "run",
            "--rm",
            f"--memory={CONTAINER_MEMORY}",
            f"--cpus={CONTAINER_CPUS}",
            f"{IMAGE_PREFIX}:{tag}",
            "bash",
            "-lc",
            verification_script(patch, repo_name),
        ],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        check=False,
    )
    output = completed.stdout + completed.stderr
    return to_verification(parse_result(completed.returncode, output))
