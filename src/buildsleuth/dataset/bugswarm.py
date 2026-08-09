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

import re
import shlex
import subprocess
from dataclasses import dataclass, field

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


def extract_script(job_id: int) -> str:
    """Read the failing log, the maintainer's fix, and which files it touched.

    Emitted with separators rather than three docker runs, because starting a
    container is the expensive part and these images are large.
    """
    project = f"$(ls {FAILED_DIR} | head -1)"
    return "\n".join(
        [
            "set -e",
            f"PROJ={project}",
            'echo "=====BUILDSLEUTH_LOG====="',
            f'cat {BUILD_ROOT}/{job_id}-orig.log 2>/dev/null || echo "no log"',
            'echo "=====BUILDSLEUTH_DIFF====="',
            f'diff -ruN "{FAILED_DIR}/$PROJ" "{PASSED_DIR}/$PROJ" 2>/dev/null | head -4000 || true',
            'echo "=====BUILDSLEUTH_FILES====="',
            f'diff -rq "{FAILED_DIR}/$PROJ" "{PASSED_DIR}/$PROJ" 2>/dev/null'
            f' | sed -n "s#^Files {FAILED_DIR}/$PROJ/\\(.*\\) and .* differ\\$#\\1#p" || true',
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
        raise ArtifactError(f"{tag} has no original build log")

    files = [line.strip() for line in sections.get("files", "").splitlines() if line.strip()]
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
