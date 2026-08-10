import base64
import json
from typing import Any

import httpx
import pytest
import respx

from buildsleuth.providers.github.client import GitHubApiError, GitHubClient
from buildsleuth.providers.github.writer import (
    AI_LABEL,
    BLOB_ENCODING,
    BLOB_TYPE,
    BRANCH_PREFIX,
    FILE_MODE,
    DraftPullRequest,
    GitHubWriter,
    branch_name,
    build_pr_body,
)

API = "https://api.github.com"
REPO = "o/r"
BLOBS_URL = f"{API}/repos/o/r/git/blobs"
TREES_URL = f"{API}/repos/o/r/git/trees"
COMMITS_URL = f"{API}/repos/o/r/git/commits"
REFS_URL = f"{API}/repos/o/r/git/refs"
PULLS_URL = f"{API}/repos/o/r/pulls"

BASE_SHA = "base0000"
BLOB_SHA = "blob1111"
TREE_SHA = "tree2222"
COMMIT_SHA = "commit33"
BRANCH = "buildsleuth/fix-42"
MESSAGE = "fix: pin the toolchain"
RUN_ID = 42
PR_NUMBER = 7
PR_URL = "https://github.com/o/r/pull/7"

# Built rather than typed so this file stays free of the character it forbids.
EM_DASH = chr(0x2014)


def make_writer() -> GitHubWriter:
    return GitHubWriter(GitHubClient(token=None))


def body_of(request: httpx.Request) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(request.content)
    return payload


def paths_of(router: respx.MockRouter) -> list[str]:
    return [call.request.url.path for call in router.calls]


def mock_git_data(router: respx.MockRouter, blob_shas: list[str]) -> dict[str, respx.Route]:
    """Route the four Git Data endpoints, one blob response per file."""
    return {
        "blobs": router.post(BLOBS_URL).mock(
            side_effect=[httpx.Response(201, json={"sha": sha}) for sha in blob_shas]
        ),
        "trees": router.post(TREES_URL).respond(201, json={"sha": TREE_SHA}),
        "commits": router.post(COMMITS_URL).respond(201, json={"sha": COMMIT_SHA}),
        "refs": router.post(REFS_URL).respond(201, json={"ref": f"refs/heads/{BRANCH}"}),
    }


def test_create_branch_commit_follows_git_data_sequence(respx_mock: respx.MockRouter) -> None:
    routes = mock_git_data(respx_mock, [BLOB_SHA])

    sha = make_writer().create_branch_commit(
        REPO, BASE_SHA, BRANCH, {"src/app.py": "print(1)\n"}, MESSAGE
    )

    assert sha == COMMIT_SHA
    assert paths_of(respx_mock) == [
        "/repos/o/r/git/blobs",
        "/repos/o/r/git/trees",
        "/repos/o/r/git/commits",
        "/repos/o/r/git/refs",
    ]
    assert body_of(routes["blobs"].calls.last.request) == {
        "content": base64.b64encode(b"print(1)\n").decode("ascii"),
        "encoding": BLOB_ENCODING,
    }
    assert body_of(routes["trees"].calls.last.request) == {
        "base_tree": BASE_SHA,
        "tree": [
            {"path": "src/app.py", "mode": FILE_MODE, "type": BLOB_TYPE, "sha": BLOB_SHA},
        ],
    }
    assert body_of(routes["commits"].calls.last.request) == {
        "message": MESSAGE,
        "tree": TREE_SHA,
        "parents": [BASE_SHA],
    }
    assert body_of(routes["refs"].calls.last.request) == {
        "ref": f"refs/heads/{BRANCH}",
        "sha": COMMIT_SHA,
    }


def test_many_files_produce_one_tree_and_one_commit(respx_mock: respx.MockRouter) -> None:
    """The atomicity property: a blob per file, but a single commit for all of them."""
    shas = ["blob-a", "blob-b", "blob-c"]
    routes = mock_git_data(respx_mock, shas)
    files = {"a.py": "a\n", "b.py": "b\n", "c/d.py": "d\n"}

    make_writer().create_branch_commit(REPO, BASE_SHA, BRANCH, files, MESSAGE)

    assert routes["blobs"].call_count == len(files)
    assert routes["trees"].call_count == 1
    assert routes["commits"].call_count == 1
    assert routes["refs"].call_count == 1
    assert body_of(routes["trees"].calls.last.request)["tree"] == [
        {"path": path, "mode": FILE_MODE, "type": BLOB_TYPE, "sha": sha}
        for path, sha in zip(files, shas, strict=True)
    ]
    assert body_of(routes["commits"].calls.last.request)["parents"] == [BASE_SHA]


def test_non_ascii_content_survives_base64_round_trip(respx_mock: respx.MockRouter) -> None:
    routes = mock_git_data(respx_mock, [BLOB_SHA])
    content = "# café naïve 日本語 ✓\nvalue = 'ü'\n"

    make_writer().create_branch_commit(REPO, BASE_SHA, BRANCH, {"a.py": content}, MESSAGE)

    sent = body_of(routes["blobs"].calls.last.request)["content"]
    assert base64.b64decode(sent).decode("utf-8") == content


def test_open_draft_pr_sends_draft_and_labels_the_new_number(respx_mock: respx.MockRouter) -> None:
    pulls = respx_mock.post(PULLS_URL).respond(201, json={"number": PR_NUMBER, "html_url": PR_URL})
    labels = respx_mock.post(f"{API}/repos/o/r/issues/{PR_NUMBER}/labels").respond(
        200, json=[{"name": AI_LABEL}]
    )

    result = make_writer().open_draft_pr(REPO, BRANCH, "main", "Fix the build", "body text")

    assert result == DraftPullRequest(number=PR_NUMBER, url=PR_URL, branch=BRANCH)
    assert body_of(pulls.calls.last.request) == {
        "title": "Fix the build",
        "head": BRANCH,
        "base": "main",
        "body": "body text",
        "draft": True,
    }
    assert body_of(labels.calls.last.request) == {"labels": [AI_LABEL]}


def test_branch_name_uses_prefix_and_run_id() -> None:
    branch = branch_name(RUN_ID)
    assert branch.startswith(BRANCH_PREFIX)
    assert str(RUN_ID) in branch
    assert branch == f"{BRANCH_PREFIX}-{RUN_ID}"


def test_blob_failure_stops_before_tree(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(BLOBS_URL).respond(403, json={"message": "no write access"})
    trees = respx_mock.post(TREES_URL).respond(201, json={"sha": TREE_SHA})

    with pytest.raises(GitHubApiError) as excinfo:
        make_writer().create_branch_commit(REPO, BASE_SHA, BRANCH, {"a.py": "a\n"}, MESSAGE)

    assert excinfo.value.status == 403
    assert not trees.called


def test_tree_failure_attempts_no_commit_and_no_ref(respx_mock: respx.MockRouter) -> None:
    """A failed tree must not leave a commit or a branch behind."""
    respx_mock.post(BLOBS_URL).respond(201, json={"sha": BLOB_SHA})
    respx_mock.post(TREES_URL).respond(422, json={"message": "base_tree is invalid"})
    commits = respx_mock.post(COMMITS_URL).respond(201, json={"sha": COMMIT_SHA})
    refs = respx_mock.post(REFS_URL).respond(201, json={})

    with pytest.raises(GitHubApiError) as excinfo:
        make_writer().create_branch_commit(REPO, BASE_SHA, BRANCH, {"a.py": "a\n"}, MESSAGE)

    assert excinfo.value.status == 422
    assert not commits.called
    assert not refs.called


def test_commit_failure_attempts_no_ref(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(BLOBS_URL).respond(201, json={"sha": BLOB_SHA})
    respx_mock.post(TREES_URL).respond(201, json={"sha": TREE_SHA})
    respx_mock.post(COMMITS_URL).respond(422, json={"message": "bad parent"})
    refs = respx_mock.post(REFS_URL).respond(201, json={})

    with pytest.raises(GitHubApiError):
        make_writer().create_branch_commit(REPO, BASE_SHA, BRANCH, {"a.py": "a\n"}, MESSAGE)

    assert not refs.called


def test_ref_failure_propagates(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(BLOBS_URL).respond(201, json={"sha": BLOB_SHA})
    respx_mock.post(TREES_URL).respond(201, json={"sha": TREE_SHA})
    respx_mock.post(COMMITS_URL).respond(201, json={"sha": COMMIT_SHA})
    respx_mock.post(REFS_URL).respond(422, json={"message": "Reference already exists"})

    with pytest.raises(GitHubApiError) as excinfo:
        make_writer().create_branch_commit(REPO, BASE_SHA, BRANCH, {"a.py": "a\n"}, MESSAGE)

    assert excinfo.value.status == 422


def test_pull_request_failure_attempts_no_label(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(PULLS_URL).respond(422, json={"message": "No commits between branches"})
    labels = respx_mock.post(f"{API}/repos/o/r/issues/{PR_NUMBER}/labels").respond(200, json=[])

    with pytest.raises(GitHubApiError) as excinfo:
        make_writer().open_draft_pr(REPO, BRANCH, "main", "t", "b")

    assert excinfo.value.status == 422
    assert not labels.called


def test_label_failure_propagates(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(PULLS_URL).respond(201, json={"number": PR_NUMBER, "html_url": PR_URL})
    respx_mock.post(f"{API}/repos/o/r/issues/{PR_NUMBER}/labels").respond(
        404, json={"message": "Not Found"}
    )

    with pytest.raises(GitHubApiError) as excinfo:
        make_writer().open_draft_pr(REPO, BRANCH, "main", "t", "b")

    assert excinfo.value.status == 404


def make_body(
    evidence: list[str] | None = None,
    verification: str = "pytest -q passed on the patched tree",
) -> str:
    return build_pr_body(
        failure_class="dependency",
        subcategory="version_conflict",
        strategy="pin urllib3 below 2.0",
        expected_effect="the resolver stops picking an incompatible urllib3",
        evidence=["line one", "line two"] if evidence is None else evidence,
        verification=verification,
        run_url="https://github.com/o/r/actions/runs/42",
        model="example-model-v1",
    )


def test_pr_body_names_the_model_and_the_classification() -> None:
    body = make_body()
    assert "example-model-v1" in body
    assert "dependency" in body
    assert "version_conflict" in body
    assert "pin urllib3 below 2.0" in body
    assert "the resolver stops picking an incompatible urllib3" in body
    assert "https://github.com/o/r/actions/runs/42" in body


def test_pr_body_quotes_at_most_three_evidence_lines() -> None:
    body = make_body(evidence=["first", "second", "third", "fourth", "fifth"])
    assert "> first" in body
    assert "> second" in body
    assert "> third" in body
    assert "fourth" not in body
    assert "fifth" not in body


def test_pr_body_renders_placeholder_for_empty_evidence() -> None:
    assert "> none recorded" in make_body(evidence=[])


def test_pr_body_includes_the_verification_text() -> None:
    assert "pytest -q passed on the patched tree" in make_body()


def test_pr_body_states_it_was_not_written_by_a_person() -> None:
    assert "not by a person" in make_body()


def test_pr_body_contains_no_em_dash() -> None:
    assert EM_DASH not in make_body(evidence=[], verification="none")
