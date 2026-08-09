"""Turn a BugSwarm artifact into a case whose fix can actually be executed.

Every other case in this benchmark stores a log and a diff, which is enough
to score classification and localization and not enough to score a patch. A
BugSwarm image carries the repository at both the failing and the fixed
commit plus scripts that reproduce each, so a proposed patch can be applied
and the build rerun.

The ground truth is better here too. Culprit files come from the maintainer's
own fix rather than from a labeller reading a log, and the class follows from
the artifact being a reproducible build failure at a commit a human then
fixed.
"""

import json
import re
import shlex
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

IMAGE_PREFIX = "bugswarm/cached-images"
BUILD_ROOT = "/home/travis/build"
FAILED_DIR = f"{BUILD_ROOT}/failed"
PASSED_DIR = f"{BUILD_ROOT}/passed"
RUN_FAILED = "/usr/local/bin/run_failed.sh"
DOCKER = "docker"
DEFAULT_TIMEOUT_SECONDS = 900
# These images carry a whole Travis environment. Left uncapped on Docker
# Desktop a container can grow until the host is starved.
CONTAINER_MEMORY = "2g"
CONTAINER_CPUS = "2"
IMAGE_REMOVE_TIMEOUT_SECONDS = 300
# The build cache sits beside the checkout and sorts before it alphabetically.
CACHE_DIR = "cacher"
# A rebuilt virtualenv differs on every path and timestamp it embeds, which
# buries the maintainer's fix under thousands of lines that are not the fix.
DIFF_EXCLUDES = ("env", ".git", "__pycache__", "node_modules", "*.pyc", ".tox", "venv")
DIFF_LINE_LIMIT = 4000
METADATA_URL = "http://www.api.bugswarm.org/v1/artifacts"
METADATA_TIMEOUT_SECONDS = 30
# The catalogue joins failing test names with a hash.
TEST_SEPARATOR = "#"
# Deep enough for failed/<owner>/<repo>, shallow enough not to match a
# directory inside the checkout that happens to share the repository name.
CHECKOUT_SEARCH_DEPTH = 2
UNKNOWN_SHA = "unknown"
# A tag looks like owner-repo-jobid, and only the job id is reliably numeric.
_TAG_RE = re.compile(r"^(?P<slug>.+)-(?P<job_id>\d+)$")


class ArtifactError(Exception):
    """The image is missing something a case needs."""


@dataclass(frozen=True)
class Artifact:
    """What a BugSwarm image yields for one case."""

    tag: str
    slug: str
    job_id: int
    failing_log: str
    fix_diff: str
    culprit_files: list[str] = field(default_factory=list)

    @property
    def image(self) -> str:
        return f"{IMAGE_PREFIX}:{self.tag}"


def parse_tag(tag: str) -> tuple[str, int]:
    """Split an image tag into its repository slug and job id."""
    match = _TAG_RE.match(tag)
    if match is None:
        raise ArtifactError(f"{tag} does not look like a BugSwarm tag")
    return match.group("slug"), int(match.group("job_id"))


@dataclass(frozen=True)
class ArtifactMetadata:
    """What the catalogue knows about an artifact without pulling the image."""

    repo: str
    head_sha: str
    failing_tests: list[str]
    test_framework: str
    changed_files: int

    @property
    def repo_name(self) -> str:
        _, _, name = self.repo.partition("/")
        return name


def parse_failing_tests(raw: str) -> list[str]:
    """Split the catalogue's failing test string into individual test names."""
    return [name.strip() for name in raw.split(TEST_SEPARATOR) if name.strip()]


def parse_metadata(record: dict[str, Any]) -> ArtifactMetadata:
    failed_job = record.get("failed_job", {})
    return ArtifactMetadata(
        repo=record.get("repo", ""),
        head_sha=failed_job.get("trigger_sha", "") or UNKNOWN_SHA,
        failing_tests=parse_failing_tests(failed_job.get("failed_tests") or ""),
        test_framework=record.get("test_framework", ""),
        changed_files=record.get("metrics", {}).get("num_of_changed_files", 0),
    )


def fetch_metadata(tag: str, timeout: int = METADATA_TIMEOUT_SECONDS) -> ArtifactMetadata | None:
    """Look the artifact up in the BugSwarm catalogue.

    Worth a request per import because it supplies the commit the build ran
    at and the names of the tests that failed. Diffing the trees in the image
    yields neither, and a case without them cannot say which test a patch is
    supposed to make pass.

    Returns None when the catalogue is unreachable or does not know the tag,
    so an import degrades to the weaker ground truth rather than failing.
    """
    record = _load_json(f"{METADATA_URL}/{urllib.parse.quote(tag)}", timeout)
    if record is None or "failed_job" not in record:
        return None
    return parse_metadata(record)


def _load_json(url: str, timeout: int) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.load(response)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def repo_name_from_slug(slug: str) -> str:
    """The checkout directory name, which is the repository half of the slug.

    BugSwarm joins owner and repository with a hyphen, and both may contain
    hyphens themselves, so this is a guess. The extraction script falls back
    to searching when the guess does not exist.
    """
    _, _, name = slug.partition("-")
    return name or slug


def in_image(tag: str, script: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> str:
    """Run a shell script inside the artifact image and return its output.

    Memory is capped because these images carry a whole Travis environment,
    and an uncapped container on Docker Desktop can grow until the host has
    nothing left. The images themselves are removed by `discard_image` once
    the case has been extracted.
    """
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
        text=True,
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0 and not completed.stdout:
        raise ArtifactError(f"{tag}: {completed.stderr.strip()[:200]}")
    return completed.stdout


def discard_image(tag: str, timeout: int = IMAGE_REMOVE_TIMEOUT_SECONDS) -> bool:
    """Delete the pulled image once its case has been written.

    A BugSwarm artifact is gigabytes. Importing a batch without removing each
    one fills the disk, and on Docker Desktop that surfaces as the host
    getting into trouble rather than as a clear docker error.
    """
    completed = subprocess.run(
        [DOCKER, "rmi", "-f", f"{IMAGE_PREFIX}:{tag}"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return completed.returncode == 0


def extract_script(job_id: int, repo_name: str) -> str:
    """Read the failing log, the maintainer's fix, and which files it touched.

    The layout is discovered rather than assumed. Artifacts built from Travis
    put everything under /home/travis/build; later ones built from GitHub
    Actions use a different root, and hardcoding the old one silently reports
    every newer artifact as having no log.

    The checkout is picked by repository name. Taking the first directory
    instead selects the build cache on these images, and the resulting diff
    describes a virtualenv rather than the maintainer's fix.

    Emitted with separators in a single script, because starting a container
    is the expensive part and these images are large.
    """
    excludes = " ".join(f"-x {shlex.quote(name)}" for name in DIFF_EXCLUDES)
    return "\n".join(
        [
            "set +e",
            # The log names the job, so find it and let it reveal the root.
            f'LOG=$(find / -maxdepth 6 -name "{job_id}-orig.log" 2>/dev/null | head -1)',
            'if [ -z "$LOG" ]; then'
            ' LOG=$(find / -maxdepth 6 -name "*-orig.log" 2>/dev/null | head -1); fi',
            'ROOT=$(dirname "$LOG" 2>/dev/null)',
            f'[ -d "$ROOT/failed" ] || ROOT={BUILD_ROOT}',
            'FAILED="$ROOT/failed"; PASSED="$ROOT/passed"',
            # Travis-era images put the checkout at failed/<repo>, later ones
            # at failed/<owner>/<repo>. Searching for it by name keeps the
            # extracted paths relative to the repository root in both, and a
            # stray leading directory would make every culprit path wrong.
            f'DIR=$(find "$FAILED" -maxdepth {CHECKOUT_SEARCH_DEPTH} -type d'
            f" -name {shlex.quote(repo_name)} 2>/dev/null | head -1)",
            'if [ -z "$DIR" ]; then DIR=$(find "$FAILED" -mindepth 1 -maxdepth 1 -type d'
            f" ! -name {shlex.quote(CACHE_DIR)} 2>/dev/null | head -1); fi",
            'PROJ=${DIR#"$FAILED/"}',
            'echo "=====BUILDSLEUTH_LOG====="',
            'cat "$LOG" 2>/dev/null || echo "no log"',
            'echo "=====BUILDSLEUTH_DIFF====="',
            f'diff -ruN {excludes} "$FAILED/$PROJ" "$PASSED/$PROJ" 2>/dev/null'
            f" | head -{DIFF_LINE_LIMIT}",
            'echo "=====BUILDSLEUTH_FILES====="',
            f'diff -rq {excludes} "$FAILED/$PROJ" "$PASSED/$PROJ" 2>/dev/null'
            ' | sed -n "s#^Files $FAILED/$PROJ/\\(.*\\) and .* differ\\$#\\1#p"',
            'echo "=====BUILDSLEUTH_LAYOUT====="',
            'echo "root=$ROOT project=$PROJ log=$LOG"',
        ]
    )


def parse_sections(output: str) -> dict[str, str]:
    """Split the extraction output back into its three parts."""
    sections: dict[str, str] = {}
    current = ""
    for line in output.splitlines():
        if line.startswith("=====BUILDSLEUTH_") and line.endswith("====="):
            current = line.strip("=").replace("BUILDSLEUTH_", "").lower()
            sections[current] = ""
            continue
        if current:
            sections[current] += line + "\n"
    return sections


def build_artifact(tag: str, output: str) -> Artifact:
    slug, job_id = parse_tag(tag)
    sections = parse_sections(output)

    log = sections.get("log", "").strip()
    if not log or log == "no log":
        # Report what the image actually looked like. The first version of
        # this said only "no log", which read as a broken artifact when the
        # real cause was an unfamiliar directory layout.
        layout = sections.get("layout", "").strip() or "layout not reported"
        raise ArtifactError(f"{tag} has no original build log ({layout})")

    files = [line.strip() for line in sections.get("files", "").splitlines() if line.strip()]
    layout = sections.get("layout", "").strip()
    if f"project={CACHE_DIR}" in layout:
        # This once passed silently and produced three cases whose ground
        # truth was a diff of a virtualenv.
        raise ArtifactError(f"{tag} resolved to the build cache, not a checkout ({layout})")

    return Artifact(
        tag=tag,
        slug=slug,
        job_id=job_id,
        failing_log=log,
        fix_diff=sections.get("diff", "").strip(),
        culprit_files=files,
    )


def verification_commands(tag: str) -> list[str]:
    """How to rerun the failing build inside the artifact image."""
    return [f"bash {shlex.quote(RUN_FAILED)}"]
