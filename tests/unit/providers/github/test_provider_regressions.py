"""Regression tests for issues found in QA review of the GitHub provider."""

import io
import zipfile

import httpx
import pytest
import respx

from buildsleuth.models.run import RunRef
from buildsleuth.providers.base import CIProvider
from buildsleuth.providers.github.client import GitHubApiError, GitHubClient
from buildsleuth.providers.github.logzip import extract_job_log
from buildsleuth.providers.github.provider import GitHubProvider
from buildsleuth.providers.github.urls import parse_run_url

API = "https://api.github.com"
TOKEN = "secret-token"


def _zip_of(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in entries.items():
            archive.writestr(name, text)
    return buffer.getvalue()


@respx.mock
def test_pagination_link_to_foreign_host_is_refused() -> None:
    """A Link header off the API host must not receive our Authorization header."""
    respx.get(f"{API}/repos/o/r/actions/runs/1/jobs").respond(
        json={"jobs": []},
        headers={"Link": '<https://evil.example.com/steal?page=2>; rel="next"'},
    )
    foreign = respx.get("https://evil.example.com/steal")

    client = GitHubClient(token=TOKEN, base_url=API)
    with pytest.raises(GitHubApiError) as error:
        client.get_jobs("o/r", 1)

    assert not foreign.called
    assert "unexpected host" in error.value.message


@respx.mock
def test_error_message_redacts_signed_url_query() -> None:
    """An expired signed log URL must not leak its signature into the error."""
    respx.get(f"{API}/repos/o/r/actions/jobs/9/logs").respond(
        302, headers={"Location": "https://blob.example.com/log.txt?sig=SECRETSIG&se=now"}
    )
    respx.get("https://blob.example.com/log.txt").respond(403, text="expired")

    client = GitHubClient(token=TOKEN, base_url=API)
    with pytest.raises(GitHubApiError) as error:
        client.get_job_log("o/r", 9)

    assert "SECRETSIG" not in str(error.value)
    assert "sig=" not in error.value.url


@respx.mock
def test_gateway_error_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub 502s are transient, so one retry should recover the request."""
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    route = respx.get(f"{API}/repos/o/r/actions/runs/1")
    route.side_effect = [
        httpx.Response(502, text="bad gateway"),
        httpx.Response(200, json={"id": 1}),
    ]

    client = GitHubClient(token=None, base_url=API)
    assert client.get_run("o/r", 1) == {"id": 1}
    assert route.call_count == 2


@respx.mock
def test_server_error_retries_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    route = respx.get(f"{API}/repos/o/r/actions/runs/1").respond(503, text="unavailable")

    client = GitHubClient(token=None, base_url=API)
    with pytest.raises(GitHubApiError):
        client.get_run("o/r", 1)
    assert route.call_count == 3  # initial attempt plus MAX_RETRIES


@respx.mock
def test_client_error_with_retry_after_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only rate-limit and gateway statuses retry; a 500 must fail immediately."""
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    route = respx.get(f"{API}/repos/o/r/actions/runs/1").respond(
        500, headers={"Retry-After": "1"}, text="boom"
    )

    client = GitHubClient(token=None, base_url=API)
    with pytest.raises(GitHubApiError):
        client.get_run("o/r", 1)
    assert route.call_count == 1


def test_short_job_name_requires_exact_match() -> None:
    """A shorter entry name must not be treated as a truncation of a longer lookup."""
    zip_bytes = _zip_of({"1_build.txt": "build log"})
    assert extract_job_log(zip_bytes, "builder") is None
    assert extract_job_log(zip_bytes, "build") == "build log"


def test_truncated_long_job_name_still_matches() -> None:
    stored = "x" * 90
    zip_bytes = _zip_of({f"1_{stored}.txt": "long log"})
    assert extract_job_log(zip_bytes, stored + " (extra suffix)") == "long log"


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/o/r/actions/runs/12/job/34?pr=99",
        "https://github.com/o/r/actions/runs/12?check_suite_focus=true",
        "https://github.com/o/r/actions/runs/12#step:3:1",
    ],
)
def test_urls_with_query_or_fragment_are_accepted(url: str) -> None:
    assert parse_run_url(url) == RunRef(repo="o/r", run_id=12, attempt=None)


@respx.mock
def test_open_pr_preferred_over_closed() -> None:
    respx.get(f"{API}/repos/o/r/commits/abc/pulls").respond(
        json=[{"number": 1, "state": "closed"}, {"number": 2, "state": "open"}]
    )
    provider = GitHubProvider(GitHubClient(token=None, base_url=API))
    assert provider.get_pr_for_commit("o/r", "abc") == 2


@respx.mock
def test_null_workflow_name_falls_back_to_path() -> None:
    respx.get(f"{API}/repos/o/r/actions/runs/1").respond(
        json={
            "name": None,
            "path": ".github/workflows/ci.yml",
            "run_attempt": 1,
            "head_sha": "abc",
            "event": "push",
            "html_url": "https://github.com/o/r/actions/runs/1",
        }
    )
    provider = GitHubProvider(GitHubClient(token=None, base_url=API))
    run = provider.get_run(RunRef(repo="o/r", run_id=1))
    assert run.workflow_name == ".github/workflows/ci.yml"


def test_provider_satisfies_ci_provider_protocol() -> None:
    """Static binding so signature drift fails type checking, not just at call sites."""
    provider: CIProvider = GitHubProvider(GitHubClient(token=None))
    assert provider is not None
