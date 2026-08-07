"""Live tests against the real GitHub API.

Excluded from the default run. Enable with `uv run pytest -m integration`,
which needs BUILDSLEUTH_GITHUB_TOKEN.

These exist because recorded payloads drift. Every unit test in this project
asserts against a shape somebody wrote down, and a shape written down in
February is a guess about what the API does in August. These check the guess.

They are read only. Nothing here writes to any repository.
"""

import os

import pytest

from buildsleuth.models.run import Conclusion, RunRef
from buildsleuth.pipeline.ingest import NoFailedJobError, ingest, pick_failed_job
from buildsleuth.providers.github.client import GitHubClient
from buildsleuth.providers.github.provider import GitHubProvider

# A public repository with high CI volume, so there is always a recent failure.
LIVE_REPO = "apache/airflow"
TOKEN_VAR = "BUILDSLEUTH_GITHUB_TOKEN"
MIN_LOG_CHARS = 100

pytestmark = pytest.mark.integration


@pytest.fixture
def client() -> GitHubClient:
    token = os.environ.get(TOKEN_VAR)
    if not token:
        pytest.skip(f"{TOKEN_VAR} is not set")
    return GitHubClient(token=token)


@pytest.fixture
def provider(client: GitHubClient) -> GitHubProvider:
    return GitHubProvider(client)


def test_failed_runs_still_have_the_fields_we_read(client: GitHubClient) -> None:
    """The run payload is the root of everything downstream."""
    runs = client.list_failed_runs(LIVE_REPO, per_page=3)
    assert runs, "no recent failures found, so this test proved nothing"

    for run in runs:
        for field in ("id", "name", "run_attempt", "head_sha", "event", "html_url"):
            assert field in run, f"the runs payload no longer carries {field}"


def test_a_real_run_maps_into_the_domain_model(
    client: GitHubClient, provider: GitHubProvider
) -> None:
    runs = client.list_failed_runs(LIVE_REPO, per_page=1)
    ref = RunRef(repo=LIVE_REPO, run_id=int(runs[0]["id"]))

    run = provider.get_run(ref)
    assert run.head_sha
    assert run.ref == ref

    jobs = provider.get_jobs(ref)
    assert jobs, "a failed run with no jobs would break every downstream stage"
    assert any(job.conclusion is not None for job in jobs)


def test_the_logs_endpoint_still_redirects_and_returns_text(
    client: GitHubClient, provider: GitHubProvider
) -> None:
    """The signed URL expires in about a minute, so this only works if we follow it at once."""
    runs = client.list_failed_runs(LIVE_REPO, per_page=5)

    for payload in runs:
        ref = RunRef(repo=LIVE_REPO, run_id=int(payload["id"]))
        jobs = provider.get_jobs(ref)
        failed = [job for job in jobs if job.conclusion is Conclusion.FAILURE]
        if not failed:
            continue

        log = provider.get_job_log(ref, failed[0])
        assert len(log) > MIN_LOG_CHARS
        return

    pytest.skip("no failed job with a log found in the sampled runs")


def test_ingest_produces_a_usable_snapshot_from_a_live_url(
    client: GitHubClient, provider: GitHubProvider
) -> None:
    """The whole read path, against the real thing."""
    runs = client.list_failed_runs(LIVE_REPO, per_page=5)

    for payload in runs:
        ref = RunRef(repo=LIVE_REPO, run_id=int(payload["id"]))
        try:
            pick_failed_job(provider.get_jobs(ref))
        except NoFailedJobError:
            continue

        snapshot = ingest(str(payload["html_url"]), provider)
        assert snapshot.log_text
        # Cleaning must have happened: raw GitHub logs start every line with a
        # timestamp, and none should survive into the snapshot.
        assert not snapshot.log_text.lstrip().startswith("20")
        assert snapshot.run.head_sha
        return

    pytest.skip("no run with a triageable failed job found in the sampled runs")


def test_a_commit_with_no_pull_request_returns_none(provider: GitHubProvider) -> None:
    """None rather than an exception is what every caller assumes."""
    assert provider.get_pr_for_commit(LIVE_REPO, "0" * 40) is None


def test_a_missing_file_returns_none(provider: GitHubProvider) -> None:
    assert provider.get_file(LIVE_REPO, "no/such/file/anywhere.txt", "main") is None
