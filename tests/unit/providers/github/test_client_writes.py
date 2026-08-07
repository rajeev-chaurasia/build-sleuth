"""Write-side client methods. Reads live in test_client.py."""

import json
from typing import Any

import httpx
import pytest
import respx

from buildsleuth.providers.github.client import GitHubApiError, GitHubClient

API = "https://api.github.com"
REPO = "o/r"
FAKE_KEY = "ghp_fake_key_never_in_errors_0123456789"


@pytest.fixture
def sleep_calls(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    calls: list[float] = []
    monkeypatch.setattr(
        "buildsleuth.providers.github.client.time.sleep",
        lambda seconds: calls.append(seconds),
    )
    return calls


def body_of(request: httpx.Request) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(request.content)
    return payload


def test_create_blob_posts_content_and_encoding(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{API}/repos/o/r/git/blobs").respond(201, json={"sha": "blob1"})
    assert GitHubClient(token=None).create_blob(REPO, "aGk=", "base64") == {"sha": "blob1"}
    assert body_of(route.calls.last.request) == {"content": "aGk=", "encoding": "base64"}


def test_create_tree_posts_base_tree_and_entries(respx_mock: respx.MockRouter) -> None:
    entries = [{"path": "a.py", "mode": "100644", "type": "blob", "sha": "blob1"}]
    route = respx_mock.post(f"{API}/repos/o/r/git/trees").respond(201, json={"sha": "tree1"})
    assert GitHubClient(token=None).create_tree(REPO, "base1", entries) == {"sha": "tree1"}
    assert body_of(route.calls.last.request) == {"base_tree": "base1", "tree": entries}


def test_create_commit_posts_message_tree_and_parents(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{API}/repos/o/r/git/commits").respond(201, json={"sha": "commit1"})
    assert GitHubClient(token=None).create_commit(REPO, "msg", "tree1", ["base1"]) == {
        "sha": "commit1"
    }
    assert body_of(route.calls.last.request) == {
        "message": "msg",
        "tree": "tree1",
        "parents": ["base1"],
    }


def test_create_ref_posts_full_ref_and_sha(respx_mock: respx.MockRouter) -> None:
    payload = {"ref": "refs/heads/topic", "object": {"sha": "commit1"}}
    route = respx_mock.post(f"{API}/repos/o/r/git/refs").respond(201, json=payload)
    assert GitHubClient(token=None).create_ref(REPO, "refs/heads/topic", "commit1") == payload
    assert body_of(route.calls.last.request) == {"ref": "refs/heads/topic", "sha": "commit1"}


def test_get_ref_hits_the_ref_endpoint(respx_mock: respx.MockRouter) -> None:
    payload = {"ref": "refs/heads/main", "object": {"sha": "abc123"}}
    route = respx_mock.get(f"{API}/repos/o/r/git/ref/heads/main").respond(200, json=payload)
    assert GitHubClient(token=None).get_ref(REPO, "heads/main") == payload
    assert route.called


def test_create_pull_request_defaults_to_draft(respx_mock: respx.MockRouter) -> None:
    payload = {"number": 7, "html_url": "https://github.com/o/r/pull/7"}
    route = respx_mock.post(f"{API}/repos/o/r/pulls").respond(201, json=payload)
    assert GitHubClient(token=None).create_pull_request(REPO, "t", "head", "main", "b") == payload
    assert body_of(route.calls.last.request) == {
        "title": "t",
        "head": "head",
        "base": "main",
        "body": "b",
        "draft": True,
    }


def test_create_pull_request_honors_explicit_draft_false(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{API}/repos/o/r/pulls").respond(201, json={"number": 7})
    GitHubClient(token=None).create_pull_request(REPO, "t", "head", "main", "b", draft=False)
    assert body_of(route.calls.last.request)["draft"] is False


def test_add_labels_uses_the_issues_endpoint(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(f"{API}/repos/o/r/issues/7/labels").respond(
        200, json=[{"name": "ai-generated"}]
    )
    result = GitHubClient(token=None).add_labels(REPO, 7, ["ai-generated"])
    assert result == [{"name": "ai-generated"}]
    assert body_of(route.calls.last.request) == {"labels": ["ai-generated"]}


def test_write_error_carries_status_and_message(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(f"{API}/repos/o/r/git/refs").respond(
        422, json={"message": "Reference already exists"}
    )
    with pytest.raises(GitHubApiError) as excinfo:
        GitHubClient(token=None).create_ref(REPO, "refs/heads/topic", "commit1")
    error = excinfo.value
    assert error.status == 422
    assert error.message == "Reference already exists"
    assert error.url.endswith("/repos/o/r/git/refs")


def test_writes_are_not_retried_on_503(
    respx_mock: respx.MockRouter, sleep_calls: list[float]
) -> None:
    """A retried create can leave a duplicate branch or pull request behind."""
    route = respx_mock.post(f"{API}/repos/o/r/pulls").respond(503, json={"message": "unavailable"})

    with pytest.raises(GitHubApiError) as excinfo:
        GitHubClient(token=None).create_pull_request(REPO, "t", "head", "main", "b")

    assert excinfo.value.status == 503
    assert route.call_count == 1
    assert sleep_calls == []


def test_reads_do_retry_503_unlike_writes(
    respx_mock: respx.MockRouter, sleep_calls: list[float]
) -> None:
    """The contrast that makes the no-retry rule on writes a deliberate choice."""
    route = respx_mock.get(f"{API}/repos/o/r/git/ref/heads/main").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"object": {"sha": "abc"}}),
        ]
    )
    assert GitHubClient(token=None).get_ref(REPO, "heads/main") == {"object": {"sha": "abc"}}
    assert route.call_count == 2
    assert sleep_calls == [1.0]


def test_api_key_absent_from_write_errors(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(f"{API}/repos/o/r/git/blobs").respond(500, json={"message": "boom"})
    with pytest.raises(GitHubApiError) as excinfo:
        GitHubClient(token=FAKE_KEY).create_blob(REPO, "aGk=", "base64")
    error = excinfo.value
    assert FAKE_KEY not in str(error)
    assert FAKE_KEY not in repr(error)
    assert FAKE_KEY not in error.message
    assert FAKE_KEY not in error.url
