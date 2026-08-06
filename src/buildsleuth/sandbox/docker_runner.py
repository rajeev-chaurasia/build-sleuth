"""Run a case's tests inside a container to see whether a patch really works.

The repository is cloned inside the container rather than mounted from the
host, which keeps Windows path, permission and line ending differences out of
the result. Tests run with networking off so a passing run cannot depend on
something being reachable today.
"""

import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

DOCKER = "docker"
WORKDIR = "/work"
PATCH_PATH = "/tmp/fix.patch"
DEFAULT_MEMORY = "2g"
DEFAULT_CPUS = "2"
DEFAULT_TIMEOUT_SECONDS = 600
OUTPUT_TAIL_CHARS = 2_000
NETWORK_NONE = "none"


@dataclass(frozen=True)
class SandboxSpec:
    """Everything needed to reproduce one case's failure in a container."""

    image: str
    repo_url: str
    head_sha: str
    setup_commands: list[str] = field(default_factory=list)
    test_command: str = ""
    regression_command: str = ""
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    output_tail: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class DockerUnavailableError(RuntimeError):
    """Docker is not installed or not running."""


def docker_available(timeout: int = 15) -> bool:
    """Whether a working Docker daemon is reachable."""
    try:
        completed = subprocess.run(
            [DOCKER, "info"], capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def build_script(spec: SandboxSpec, apply_patch: bool, network_off_for_tests: bool) -> str:
    """The shell script the container runs.

    Setup needs the network to install things; the tests themselves must not,
    so that a green result cannot be a network fluke. Docker has no way to
    drop networking mid-container, so the caller runs setup and tests as
    separate containers when isolation matters.
    """
    lines = [
        "set -eux",
        f"git clone --filter=blob:none {shlex.quote(spec.repo_url)} {WORKDIR}",
        f"cd {WORKDIR}",
        f"git checkout {shlex.quote(spec.head_sha)}",
    ]
    if apply_patch:
        lines.append(f"git apply {PATCH_PATH}")
    lines.extend(spec.setup_commands)
    if spec.test_command:
        lines.append(spec.test_command)
    if network_off_for_tests and spec.regression_command:
        lines.append(spec.regression_command)
    return "\n".join(lines)


def docker_command(spec: SandboxSpec, script: str, network: str | None = None) -> list[str]:
    """Assemble the docker invocation, resource limits included."""
    command = [
        DOCKER,
        "run",
        "--rm",
        f"--memory={DEFAULT_MEMORY}",
        f"--cpus={DEFAULT_CPUS}",
    ]
    if network:
        command.append(f"--network={network}")
    command.extend([spec.image, "bash", "-lc", script])
    return command


def run_in_container(spec: SandboxSpec, script: str, network: str | None = None) -> CommandResult:
    """Execute a script in a fresh container and report how it went."""
    if not docker_available():
        raise DockerUnavailableError("docker is not available, so a patch cannot be executed")

    try:
        completed = subprocess.run(
            docker_command(spec, script, network),
            capture_output=True,
            text=True,
            timeout=spec.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(exit_code=124, output_tail=f"timed out after {spec.timeout_seconds}s")
    except OSError as error:
        raise DockerUnavailableError(f"could not start docker: {error}") from error

    combined = (completed.stdout or "") + (completed.stderr or "")
    return CommandResult(exit_code=completed.returncode, output_tail=combined[-OUTPUT_TAIL_CHARS:])


def write_patch(patch: str, work_dir: Path) -> Path:
    """Write the patch where the container mount expects it."""
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / "fix.patch"
    path.write_text(patch, encoding="utf-8", newline="\n")
    return path
