"""Resolving a commit to its pull request, including when the commit is gone.

The 422 case was found by a live test. The unit tests had mocked a 200 with
an empty list, which is a shape nobody at GitHub ever returns.
"""

import httpx
import pytest
import respx

from buildsleuth.providers.github.client import GitHubApiError, GitHubClient
from buildsleuth.providers.github.provider import GitHubProvider

REPO = "octo/demo"
SHA = "a" * 40
URL = f"https://api.github.com/repos/{REPO}/commits/{SHA}/pulls"
MISSING_COMMIT_BODY = {"message": f"No commit found for SHA: {SHA}", "status": "422"}


def _provider() -> GitHubProvider:
    return GitHubProvider(GitHubClient(token="k"))


@respx.mock
def test_a_commit_that_no_longer_exists_reads_as_no_pull_request() -> None:
    """Force pushing a branch after a run must not cost the triage."""
    respx.get(URL).respond(422, json=MISSING_COMMIT_BODY)
    assert _provider().get_pr_for_commit(REPO, SHA) is None


@respx.mock
def test_a_commit_in_a_repository_we_cannot_see_reads_the_same_way() -> None:
    respx.get(URL).respond(404, json={"message": "Not Found"})
    assert _provider().get_pr_for_commit(REPO, SHA) is None


@respx.mock
def test_a_commit_with_no_pull_requests_returns_none() -> None:
    respx.get(URL).respond(200, json=[])
    assert _provider().get_pr_for_commit(REPO, SHA) is None


@respx.mock
def test_an_open_pull_request_is_preferred_over_a_closed_one() -> None:
    respx.get(URL).respond(
        200,
        json=[
            {"number": 11, "state": "closed"},
            {"number": 22, "state": "open"},
        ],
    )
    assert _provider().get_pr_for_commit(REPO, SHA) == 22


@respx.mock
def test_a_real_error_still_raises() -> None:
    """Swallowing 422 must not turn into swallowing everything."""
    respx.get(URL).respond(500, json={"message": "boom"})
    with pytest.raises(GitHubApiError) as error:
        _provider().get_pr_for_commit(REPO, SHA)
    assert error.value.status == 500


@respx.mock
def test_ingest_survives_a_commit_that_moved() -> None:
    """The whole point: a missing commit costs the diff, not the triage."""
    from buildsleuth.pipeline.ingest import ingest

    run_id = 5
    respx.get(f"https://api.github.com/repos/{REPO}/actions/runs/{run_id}").respond(
        200,
        json={
            "name": "CI",
            "run_attempt": 1,
            "head_sha": SHA,
            "conclusion": "failure",
            "event": "pull_request",
            "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}",
        },
    )
    respx.get(f"https://api.github.com/repos/{REPO}/actions/runs/{run_id}/jobs").respond(
        200,
        json={
            "jobs": [
                {
                    "id": 9,
                    "name": "checks",
                    "conclusion": "failure",
                    "steps": [{"number": 1, "name": "Tests", "conclusion": "failure"}],
                }
            ]
        },
    )
    respx.get(f"https://api.github.com/repos/{REPO}/actions/jobs/9/logs").respond(
        200, text="2026-08-05T10:00:00.0000000Z FAILED tests/test_x.py::test_y\n"
    )
    respx.get(URL).respond(422, json=MISSING_COMMIT_BODY)

    provider = _provider()
    snapshot = ingest(f"https://github.com/{REPO}/actions/runs/{run_id}", provider)

    assert snapshot.pr_number is None
    assert snapshot.diff_text is None
    assert "FAILED tests/test_x.py::test_y" in snapshot.log_text
    assert not snapshot.log_text.startswith("2026")


def test_the_no_commit_statuses_are_the_ones_github_actually_sends() -> None:
    from buildsleuth.providers.github.client import _NO_COMMIT_STATUSES

    assert httpx.codes.UNPROCESSABLE_ENTITY in _NO_COMMIT_STATUSES
    assert httpx.codes.NOT_FOUND in _NO_COMMIT_STATUSES
    assert httpx.codes.INTERNAL_SERVER_ERROR not in _NO_COMMIT_STATUSES
