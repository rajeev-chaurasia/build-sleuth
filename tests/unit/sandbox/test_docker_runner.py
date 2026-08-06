"""Sandbox runner: script assembly, quoting, docker flags, and failure handling.

Nothing here starts a container. subprocess.run is replaced so the tests assert
on the exact command that would have been run.
"""

import subprocess
from pathlib import Path

import pytest

from buildsleuth.sandbox import docker_runner
from buildsleuth.sandbox.docker_runner import (
    DEFAULT_CPUS,
    DEFAULT_MEMORY,
    DOCKER,
    NETWORK_NONE,
    OUTPUT_TAIL_CHARS,
    PATCH_PATH,
    WORKDIR,
    CommandResult,
    DockerUnavailableError,
    SandboxSpec,
    build_script,
    docker_available,
    docker_command,
    run_in_container,
    write_patch,
)

IMAGE = "python:3.12-slim"
REPO_URL = "https://example.com/octo/demo.git"
HEAD_SHA = "0123456789abcdef"
# Quoting has to survive both of these reaching a bash -lc string.
HOSTILE_URL = "https://example.com/demo.git;whoami"
HOSTILE_SHA = "0123456; rm -rf /"
SETUP = ["pip install -e .", "pip install pytest"]
TEST_COMMAND = "pytest -q tests/test_app.py"
REGRESSION_COMMAND = "pytest -q"
SCRIPT = "echo hello"
TIMED_OUT_EXIT_CODE = 124


class FakeRun:
    """Records the commands it was handed instead of executing them."""

    def __init__(
        self,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        error: Exception | None = None,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(command))
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(command, self.returncode, self.stdout, self.stderr)


def _spec(
    repo_url: str = REPO_URL,
    head_sha: str = HEAD_SHA,
    setup_commands: list[str] | None = None,
    test_command: str = TEST_COMMAND,
    regression_command: str = "",
    timeout_seconds: int = 600,
) -> SandboxSpec:
    return SandboxSpec(
        image=IMAGE,
        repo_url=repo_url,
        head_sha=head_sha,
        setup_commands=list(setup_commands) if setup_commands else [],
        test_command=test_command,
        regression_command=regression_command,
        timeout_seconds=timeout_seconds,
    )


def _install(monkeypatch: pytest.MonkeyPatch, fake: FakeRun) -> FakeRun:
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


def test_the_script_clones_and_checks_out_the_head_sha() -> None:
    lines = build_script(_spec(), apply_patch=False, run_regression=False).splitlines()

    assert f"git clone --filter=blob:none {REPO_URL} {WORKDIR}" in lines
    assert f"cd {WORKDIR}" in lines
    assert f"git checkout {HEAD_SHA}" in lines


def test_the_script_quotes_a_hostile_url_and_sha() -> None:
    """These values come from case metadata, so they are never trusted verbatim."""
    lines = build_script(
        _spec(repo_url=HOSTILE_URL, head_sha=HOSTILE_SHA),
        apply_patch=False,
        run_regression=False,
    ).splitlines()

    assert f"git clone --filter=blob:none '{HOSTILE_URL}' {WORKDIR}" in lines
    assert f"git checkout '{HOSTILE_SHA}'" in lines
    assert f"git checkout {HOSTILE_SHA}" not in lines


def test_the_script_applies_the_patch_only_when_asked() -> None:
    with_patch = build_script(_spec(), apply_patch=True, run_regression=False)
    without_patch = build_script(_spec(), apply_patch=False, run_regression=False)

    assert f"git apply {PATCH_PATH}" in with_patch.splitlines()
    assert PATCH_PATH not in without_patch


def test_the_patch_is_applied_after_checkout_and_before_setup() -> None:
    lines = build_script(
        _spec(setup_commands=SETUP), apply_patch=True, run_regression=False
    ).splitlines()

    assert lines.index(f"git checkout {HEAD_SHA}") < lines.index(f"git apply {PATCH_PATH}")
    assert lines.index(f"git apply {PATCH_PATH}") < lines.index(SETUP[0])


def test_setup_runs_in_order_before_the_test_command() -> None:
    lines = build_script(
        _spec(setup_commands=SETUP), apply_patch=False, run_regression=False
    ).splitlines()

    assert lines.index(SETUP[0]) < lines.index(SETUP[1]) < lines.index(TEST_COMMAND)


def test_an_absent_test_command_leaves_no_blank_line() -> None:
    lines = build_script(
        _spec(test_command=""), apply_patch=False, run_regression=False
    ).splitlines()

    assert "" not in lines


def test_the_regression_command_runs_only_when_asked() -> None:
    spec = _spec(regression_command=REGRESSION_COMMAND)
    requested = build_script(spec, apply_patch=False, run_regression=True).splitlines()
    skipped = build_script(spec, apply_patch=False, run_regression=False).splitlines()

    assert requested.index(TEST_COMMAND) < requested.index(REGRESSION_COMMAND)
    assert REGRESSION_COMMAND not in skipped


def test_an_empty_regression_command_is_never_added() -> None:
    lines = build_script(_spec(), apply_patch=False, run_regression=True).splitlines()

    assert lines[-1] == TEST_COMMAND


def test_docker_command_carries_the_resource_limits_and_image() -> None:
    command = docker_command(_spec(), SCRIPT)

    assert command[:2] == [DOCKER, "run"]
    assert "--rm" in command
    assert f"--memory={DEFAULT_MEMORY}" in command
    assert f"--cpus={DEFAULT_CPUS}" in command
    assert command[-4:] == [IMAGE, "bash", "-lc", SCRIPT]


def test_docker_command_adds_a_network_flag_only_when_a_network_is_given() -> None:
    """Tests run with networking off so a green result cannot be a network fluke."""
    isolated = docker_command(_spec(), SCRIPT, network=NETWORK_NONE)
    default = docker_command(_spec(), SCRIPT)

    assert f"--network={NETWORK_NONE}" in isolated
    assert isolated.index(f"--network={NETWORK_NONE}") < isolated.index(IMAGE)
    assert not [flag for flag in default if flag.startswith("--network")]


def test_a_run_reports_the_exit_code_and_combined_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_runner, "docker_available", lambda: True)
    fake = _install(monkeypatch, FakeRun(returncode=7, stdout="out\n", stderr="err\n"))

    result = run_in_container(_spec(), SCRIPT, network=NETWORK_NONE)

    assert result.exit_code == 7
    assert result.ok is False
    assert result.output_tail == "out\nerr\n"
    assert fake.commands == [docker_command(_spec(), SCRIPT, network=NETWORK_NONE)]


def test_a_run_keeps_only_the_tail_of_a_huge_log(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_runner, "docker_available", lambda: True)
    _install(monkeypatch, FakeRun(stdout="a" * OUTPUT_TAIL_CHARS, stderr="b" * 50))

    result = run_in_container(_spec(), SCRIPT)

    assert result.ok is True
    assert len(result.output_tail) == OUTPUT_TAIL_CHARS
    assert result.output_tail.endswith("b" * 50)


def test_a_timeout_is_reported_rather_than_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung test suite is a result about the patch, not a crash of the harness."""
    monkeypatch.setattr(docker_runner, "docker_available", lambda: True)
    _install(monkeypatch, FakeRun(error=subprocess.TimeoutExpired(cmd=[DOCKER], timeout=42)))

    result = run_in_container(_spec(timeout_seconds=42), SCRIPT)

    assert result == CommandResult(exit_code=TIMED_OUT_EXIT_CODE, output_tail="timed out after 42s")


def test_a_failure_to_start_docker_is_reported_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(docker_runner, "docker_available", lambda: True)
    _install(monkeypatch, FakeRun(error=OSError("no such executable")))

    with pytest.raises(DockerUnavailableError, match="could not start docker"):
        run_in_container(_spec(), SCRIPT)


def test_no_docker_means_no_invocation_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_runner, "docker_available", lambda: False)
    fake = _install(monkeypatch, FakeRun())

    with pytest.raises(DockerUnavailableError):
        run_in_container(_spec(), SCRIPT)

    assert fake.commands == []


def test_docker_is_available_when_info_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install(monkeypatch, FakeRun(returncode=0))

    assert docker_available() is True
    assert fake.commands == [[DOCKER, "info"]]


@pytest.mark.parametrize(
    "fake",
    [
        FakeRun(returncode=1),
        FakeRun(error=OSError("docker not installed")),
        FakeRun(error=subprocess.TimeoutExpired(cmd=[DOCKER], timeout=15)),
    ],
)
def test_docker_is_unavailable_when_info_fails(
    monkeypatch: pytest.MonkeyPatch, fake: FakeRun
) -> None:
    """A dead or missing daemon is an answer, not an exception to handle at every call site."""
    _install(monkeypatch, fake)

    assert docker_available() is False


def test_write_patch_uses_lf_endings_on_every_platform(tmp_path: Path) -> None:
    """git apply inside a Linux container cannot read a CRLF patch."""
    path = write_patch("--- a/app.py\n+++ b/app.py\n", tmp_path)

    assert b"\r\n" not in path.read_bytes()


def test_write_patch_creates_the_directory(tmp_path: Path) -> None:
    path = write_patch("--- a/app.py\n", tmp_path / "missing" / "nested")

    assert path.is_file()
    assert path.read_bytes().decode("utf-8") == "--- a/app.py\n"
