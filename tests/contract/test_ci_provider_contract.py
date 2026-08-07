"""Semantics every CIProvider must honour, independent of which CI system it wraps.

Each provider supplies a backend: a factory for the provider plus the HTTP
stubs that put it in the state a given assertion needs. The assertions
themselves never mention GitHub.

To add a provider, implement ContractBackend for it and append an instance to
BACKENDS. Nothing else in this file changes.
"""

from typing import Any, Protocol

import pytest
import respx

from buildsleuth.models.run import Conclusion, Job, RunRef, WorkflowRun
from buildsleuth.providers.base import CIProvider
from buildsleuth.providers.github.client import GitHubClient
from buildsleuth.providers.github.provider import GitHubProvider

GITHUB_API = "https://api.github.com"
REPO = "octo/demo"
RUN_ID = 42
HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
FAILING_JOB_NAME = "build"
FAILED_STEP_NAMES = ("Run tests",)
MISSING_PATH = "does/not/exist.txt"
FILE_REF = "main"


class ContractBackend(Protocol):
    """One CI system under contract test, plus the stubs its transport needs."""

    name: str
    ref: RunRef
    run_url: str
    invalid_urls: tuple[str, ...]
    failing_job_name: str
    failed_step_names: tuple[str, ...]
    unknown_conclusion: str
    unknown_conclusion_job_name: str
    missing_file_path: str
    file_ref: str

    def provider(self) -> CIProvider: ...

    def stub_run(self, mock: respx.MockRouter, conclusion: str | None) -> None: ...

    def stub_jobs(self, mock: respx.MockRouter) -> None: ...

    def stub_commit_without_pull_request(self, mock: respx.MockRouter) -> None: ...

    def stub_missing_file(self, mock: respx.MockRouter) -> None: ...


class GitHubBackend:
    """GitHub Actions, backed by respx with recorded-shape REST payloads."""

    name: str = "github"
    ref: RunRef = RunRef(repo=REPO, run_id=RUN_ID)
    run_url: str = f"https://github.com/{REPO}/actions/runs/{RUN_ID}"
    invalid_urls: tuple[str, ...] = (
        "https://gitlab.com/octo/demo/-/pipelines/42",
        "https://github.com/octo/demo/actions/runs/not-a-number",
    )
    failing_job_name: str = FAILING_JOB_NAME
    failed_step_names: tuple[str, ...] = FAILED_STEP_NAMES
    # A real GitHub conclusion that the domain enum deliberately does not carry.
    unknown_conclusion: str = "startup_failure"
    unknown_conclusion_job_name: str = "publish"
    missing_file_path: str = MISSING_PATH
    file_ref: str = FILE_REF

    def provider(self) -> CIProvider:
        return GitHubProvider(GitHubClient(token=None, base_url=GITHUB_API))

    def stub_run(self, mock: respx.MockRouter, conclusion: str | None) -> None:
        payload: dict[str, Any] = {
            "id": RUN_ID,
            "name": "CI",
            "run_attempt": 1,
            "head_sha": HEAD_SHA,
            "head_branch": "feature/widget",
            "conclusion": conclusion,
            "event": "pull_request",
            "html_url": self.run_url,
        }
        mock.get(f"{GITHUB_API}/repos/{REPO}/actions/runs/{RUN_ID}").respond(json=payload)

    def stub_jobs(self, mock: respx.MockRouter) -> None:
        payload: dict[str, Any] = {
            "jobs": [
                {
                    "id": 1,
                    "name": FAILING_JOB_NAME,
                    "conclusion": "failure",
                    "steps": [
                        {"number": 1, "name": "Set up job", "conclusion": "success"},
                        {"number": 2, "name": "Run tests", "conclusion": "failure"},
                        {"number": 3, "name": "Upload artifacts", "conclusion": "skipped"},
                        {"number": 4, "name": "Complete job", "conclusion": None},
                    ],
                },
                {
                    "id": 2,
                    "name": self.unknown_conclusion_job_name,
                    "conclusion": self.unknown_conclusion,
                    "steps": [
                        {"number": 1, "name": "Publish", "conclusion": self.unknown_conclusion}
                    ],
                },
            ]
        }
        mock.get(f"{GITHUB_API}/repos/{REPO}/actions/runs/{RUN_ID}/jobs").respond(json=payload)

    def stub_commit_without_pull_request(self, mock: respx.MockRouter) -> None:
        mock.get(f"{GITHUB_API}/repos/{REPO}/commits/{HEAD_SHA}/pulls").respond(json=[])

    def stub_missing_file(self, mock: respx.MockRouter) -> None:
        mock.get(f"{GITHUB_API}/repos/{REPO}/contents/{MISSING_PATH}").respond(
            404, json={"message": "Not Found"}
        )


BACKENDS: list[ContractBackend] = [GitHubBackend()]


@pytest.fixture(params=BACKENDS, ids=lambda backend: backend.name)
def backend(request: pytest.FixtureRequest) -> ContractBackend:
    current: ContractBackend = request.param
    return current


@pytest.fixture
def provider(backend: ContractBackend) -> CIProvider:
    return backend.provider()


def test_parse_run_url_round_trips_repo_and_run_id(
    backend: ContractBackend, provider: CIProvider
) -> None:
    ref = provider.parse_run_url(backend.run_url)

    assert ref.repo == backend.ref.repo
    assert ref.run_id == backend.ref.run_id


def test_parse_run_url_rejects_urls_it_does_not_own(
    backend: ContractBackend, provider: CIProvider
) -> None:
    """A foreign host or a malformed path must be a ValueError, never a wrong RunRef."""
    for url in backend.invalid_urls:
        with pytest.raises(ValueError):
            provider.parse_run_url(url)


def test_get_run_echoes_the_ref_it_was_asked_about(
    backend: ContractBackend, provider: CIProvider, respx_mock: respx.MockRouter
) -> None:
    backend.stub_run(respx_mock, "failure")

    run = provider.get_run(backend.ref)

    assert isinstance(run, WorkflowRun)
    assert run.ref == backend.ref


def test_get_jobs_returns_domain_jobs(
    backend: ContractBackend, provider: CIProvider, respx_mock: respx.MockRouter
) -> None:
    backend.stub_jobs(respx_mock)

    jobs = provider.get_jobs(backend.ref)

    assert jobs
    assert all(isinstance(job, Job) for job in jobs)


def test_failed_steps_reports_only_failed_steps(
    backend: ContractBackend, provider: CIProvider, respx_mock: respx.MockRouter
) -> None:
    backend.stub_jobs(respx_mock)

    jobs = provider.get_jobs(backend.ref)
    failing = next(job for job in jobs if job.name == backend.failing_job_name)

    assert [step.name for step in failing.failed_steps()] == list(backend.failed_step_names)
    assert all(step.conclusion is Conclusion.FAILURE for step in failing.failed_steps())
    assert len(failing.steps) > len(failing.failed_steps())


def test_get_pr_for_commit_returns_none_when_there_is_no_pull_request(
    backend: ContractBackend, provider: CIProvider, respx_mock: respx.MockRouter
) -> None:
    """None, not zero and not an exception: callers branch on it to decide about diffs."""
    backend.stub_commit_without_pull_request(respx_mock)

    assert provider.get_pr_for_commit(backend.ref.repo, HEAD_SHA) is None


def test_get_file_returns_none_for_a_path_that_does_not_exist(
    backend: ContractBackend, provider: CIProvider, respx_mock: respx.MockRouter
) -> None:
    backend.stub_missing_file(respx_mock)

    assert provider.get_file(backend.ref.repo, backend.missing_file_path, backend.file_ref) is None


def test_an_unknown_run_conclusion_maps_to_none(
    backend: ContractBackend, provider: CIProvider, respx_mock: respx.MockRouter
) -> None:
    """CI systems add conclusions over time, and an unseen one must not break ingest."""
    backend.stub_run(respx_mock, backend.unknown_conclusion)

    assert provider.get_run(backend.ref).conclusion is None


def test_an_unknown_job_or_step_conclusion_maps_to_none(
    backend: ContractBackend, provider: CIProvider, respx_mock: respx.MockRouter
) -> None:
    backend.stub_jobs(respx_mock)

    jobs = provider.get_jobs(backend.ref)
    unknown = next(job for job in jobs if job.name == backend.unknown_conclusion_job_name)

    assert unknown.conclusion is None
    assert [step.conclusion for step in unknown.steps] == [None]
