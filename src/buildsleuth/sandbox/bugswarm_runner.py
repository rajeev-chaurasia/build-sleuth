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
from buildsleuth.pipeline.patch_repair import repair_patch
from buildsleuth.pipeline.verify import VerificationLevel, VerificationResult

BUILD_ROOT = "/home/travis/build"
PATCH_PATH = "/tmp/buildsleuth.patch"
REPAIRED_PATH = "/tmp/buildsleuth.repaired.patch"
# The marker carries which patch went in, so a repair can only be credited
# when the model's own patch was tried first and failed.
APPLIED_RAW = 0
APPLIED_NEITHER = 1
APPLIED_REPAIRED = 2
DEFAULT_TIMEOUT_SECONDS = 1800
OUTPUT_TAIL_CHARS = 800
APPLY_MARKER = "=====BUILDSLEUTH_APPLIED====="
# Enough for a rejected-hunk report, which is what git prints on failure.
APPLY_OUTPUT_LINES = 20
FILE_MARKER = "=====BUILDSLEUTH_FILE:"
READ_TIMEOUT_SECONDS = 600
# checkout_resolution_lines quotes what it is given, so the repository is
# substituted after the script is built rather than interpolated into it.
REPO_PLACEHOLDER = "BUILDSLEUTH_REPO_PLACEHOLDER"


def terminate(patch: str) -> str:
    """Add the trailing newline git needs, and change nothing else.

    Deliberately not `normalize_patch`, which also rewrites CRLF to LF. That
    is right for a patch written on Windows against a checkout that uses LF,
    and wrong here: some of these repositories are CRLF throughout, and
    stripping the carriage returns makes every context line match nothing.
    """
    return patch if patch.endswith(("\n", "\r")) else patch + "\n"


@dataclass(frozen=True)
class ReproductionResult:
    """What happened when the patched build was rerun."""

    applied: bool
    build_passed: bool
    output: str
    # What the apply step itself said. Kept apart from the rest because a
    # rerun prints thousands of lines afterwards and the reason a patch was
    # rejected is four words near the start.
    apply_output: str = ""
    # Whether the model's own patch was rejected and only the version with
    # recomputed hunk headers went in.
    needed_repair: bool = False


def verification_script(patch: str, repo_name: str) -> str:
    """Apply the patch to the failing checkout and rerun the original job.

    The patch travels base64 encoded inside the script. Passing it on stdin
    competes with whatever the build script reads, and quoting a diff into a
    shell command corrupts it in ways that look like the model's fault.
    """
    written = terminate(patch)
    encoded = base64.b64encode(written.encode("utf-8")).decode("ascii")
    repaired = repair_patch(written)
    encoded_repaired = base64.b64encode(repaired.encode("utf-8")).decode("ascii")

    return "\n".join(
        [
            "set +e",
            'LOG=$(find / -maxdepth 6 -name "*-orig.log" 2>/dev/null | head -1)',
            'ROOT=$(dirname "$LOG" 2>/dev/null)',
            f'[ -d "$ROOT/failed" ] || ROOT={BUILD_ROOT}',
            'FAILED="$ROOT/failed"',
            *checkout_resolution_lines(repo_name),
            f"echo {encoded} | base64 -d > {PATCH_PATH}",
            f"echo {encoded_repaired} | base64 -d > {REPAIRED_PATH}",
            'cd "$FAILED/$PROJ" || exit 90',
            # Each patch is tried under both line ending conventions. Some of
            # these repositories are CRLF throughout and some are LF, and a
            # patch whose endings disagree with the file has every hunk
            # rejected, which measures the repository rather than the patch.
            # Only git apply, and only on the three line ending forms. The
            # earlier chain ended in `patch -l`, which matches context fuzzily
            # and so accepted a patch whose context line did not exist in the
            # file at all. The control caught it: a deliberately broken patch
            # applied and the build passed on 16 of 22 cases, which would have
            # inflated every apply rate the funnel reports.
            "try_apply() {",
            '  P="$1"',
            '  sed \'s/\\r$//\' "$P" > "$P.lf"',
            '  sed \'s/$/\\r/\' "$P.lf" > "$P.crlf"',
            '  git apply --whitespace=nowarn "$P" 2>&1 && return 0',
            '  git apply --whitespace=nowarn "$P.lf" 2>&1 && return 0',
            '  git apply --whitespace=nowarn "$P.crlf" 2>&1 && return 0',
            "  return 1",
            "}",
            # The model's patch exactly as written comes first, so the repair
            # can only ever be credited when the original genuinely failed.
            f"try_apply {PATCH_PATH}",
            f"if [ $? -eq 0 ]; then APPLIED={APPLIED_RAW}",
            "else",
            # patch is not atomic, so undo anything a partial attempt left.
            "  git checkout -- . 2>/dev/null",
            f"  try_apply {REPAIRED_PATH}",
            f"  if [ $? -eq 0 ]; then APPLIED={APPLIED_REPAIRED}",
            f"  else APPLIED={APPLIED_NEITHER}; fi",
            "fi",
            f'echo "{APPLY_MARKER}$APPLIED"',
            f'[ "$APPLIED" -ne {APPLIED_NEITHER} ] || exit 91',
            f"bash {shlex.quote(RUN_FAILED)} 2>&1",
        ]
    )


def read_script(paths: list[str]) -> str:
    """Print the named files as they exist in the failing checkout."""
    lines = [
        "set +e",
        'LOG=$(find / -maxdepth 6 -name "*-orig.log" 2>/dev/null | head -1)',
        'ROOT=$(dirname "$LOG" 2>/dev/null)',
        f'[ -d "$ROOT/failed" ] || ROOT={BUILD_ROOT}',
        'FAILED="$ROOT/failed"',
    ]
    lines.extend(checkout_resolution_lines(REPO_PLACEHOLDER))
    lines.append('cd "$FAILED/$PROJ" || exit 90')
    for path in paths:
        quoted = shlex.quote(path)
        lines.append(f'echo "{FILE_MARKER}{path}"')
        lines.append(f"cat {quoted} 2>/dev/null")
    return "\n".join(lines)


def parse_files(output: str) -> dict[str, str]:
    """Split the printed files back apart, keeping their line endings."""
    files: dict[str, str] = {}
    current = ""
    for line in output.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if stripped.startswith(FILE_MARKER):
            current = stripped[len(FILE_MARKER) :]
            files[current] = ""
            continue
        if current:
            files[current] += line
    return {path: body for path, body in files.items() if body.strip()}


def read_files_in_image(
    tag: str, repo_name: str, paths: list[str], timeout: int = READ_TIMEOUT_SECONDS
) -> dict[str, str]:
    """Read files out of the artifact, not out of the upstream repository.

    These images are not a plain checkout. BugSwarm patches a repository so
    the build reproduces, so the file at the same commit on GitHub can differ
    from the file the patch will be applied to. Handing the model the GitHub
    copy made it write patches whose context matched nothing, and two cases
    that had been fixed cleanly stopped applying at all.
    """
    if not paths:
        return {}
    script = read_script(paths).replace(REPO_PLACEHOLDER, repo_name)
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
            script,
        ],
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return parse_files(completed.stdout.decode("utf-8", errors="replace"))


def parse_result(returncode: int, output: str) -> ReproductionResult:
    """Read the marker rather than trusting the exit code alone.

    The reproduction script can exit non-zero for reasons that have nothing to
    do with the patch, and counting those as a failed fix would understate the
    model. The marker says whether the patch itself went in.
    """
    applied = False
    repaired = False
    lines = output.splitlines()
    marker_at = -1
    for index, line in enumerate(lines):
        if line.startswith(APPLY_MARKER):
            code = line[len(APPLY_MARKER) :].strip()
            applied = code in (str(APPLIED_RAW), str(APPLIED_REPAIRED))
            repaired = code == str(APPLIED_REPAIRED)
            marker_at = index

    apply_output = ""
    if marker_at > 0:
        apply_output = "\n".join(lines[max(0, marker_at - APPLY_OUTPUT_LINES) : marker_at])

    return ReproductionResult(
        applied=applied,
        build_passed=applied and returncode == 0,
        output=output,
        apply_output=apply_output.strip(),
        needed_repair=repaired,
    )


def to_verification(result: ReproductionResult) -> VerificationResult:
    """Map a rerun onto the hierarchy the rest of the eval reports."""
    tail = result.output[-OUTPUT_TAIL_CHARS:]
    if not result.applied:
        # The apply output rather than the tail: a rerun prints thousands of
        # lines afterwards and buries the four words that say why.
        return VerificationResult(
            VerificationLevel.NOTHING,
            "patch does not apply",
            result.apply_output[-OUTPUT_TAIL_CHARS:],
        )
    suffix = " after repairing its hunk headers" if result.needed_repair else ""
    if not result.build_passed:
        # APPLIES rather than LINTS: this path reruns the job and never lints,
        # so claiming the patch linted would report a check that never ran.
        return VerificationResult(
            VerificationLevel.APPLIES, f"patch applies{suffix} but the build still fails", tail
        )
    # The script reruns the whole job, so a pass covers the rest of the suite.
    return VerificationResult(
        VerificationLevel.NOTHING_ELSE_BROKE,
        f"the original job passes with the patch applied{suffix}",
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
    # stderr first, because docker writes image pull progress there and the
    # tail is what gets kept. Putting it last buried every real error under a
    # list of downloaded layers.
    output = completed.stderr + completed.stdout
    return to_verification(parse_result(completed.returncode, output))
