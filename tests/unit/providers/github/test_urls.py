import pytest

from buildsleuth.models.run import RunRef
from buildsleuth.providers.github.urls import parse_run_url

VALID_CASES = [
    (
        "https://github.com/octo/repo/actions/runs/123",
        RunRef(repo="octo/repo", run_id=123),
    ),
    (
        "https://github.com/octo/repo/actions/runs/123/",
        RunRef(repo="octo/repo", run_id=123),
    ),
    (
        "https://github.com/octo/repo/actions/runs/123/attempts/2",
        RunRef(repo="octo/repo", run_id=123, attempt=2),
    ),
    (
        "https://github.com/octo/repo/actions/runs/123/job/456",
        RunRef(repo="octo/repo", run_id=123),
    ),
    (
        "https://github.com/octo/repo/actions/runs/123/attempts/2/job/456",
        RunRef(repo="octo/repo", run_id=123, attempt=2),
    ),
    (
        "https://github.com/my-org/my.repo_x/actions/runs/9876543210",
        RunRef(repo="my-org/my.repo_x", run_id=9876543210),
    ),
    (
        "  https://github.com/octo/repo/actions/runs/123  ",
        RunRef(repo="octo/repo", run_id=123),
    ),
]

INVALID_URLS = [
    "",
    "not a url",
    "http://github.com/octo/repo/actions/runs/123",
    "https://gitlab.com/octo/repo/actions/runs/123",
    "https://github.com/octo/repo",
    "https://github.com/octo/repo/actions/runs/",
    "https://github.com/octo/repo/actions/runs/abc",
    "https://github.com/octo/repo/actions/runs/123/artifacts",
    "https://github.com/octo/repo/actions/runs/123/attempts/x",
    "https://github.com/octo/repo/actions/runs/123/job/",
    "https://github.com/octo/actions/runs/123",
]


@pytest.mark.parametrize(("url", "expected"), VALID_CASES)
def test_parse_run_url_valid(url: str, expected: RunRef) -> None:
    assert parse_run_url(url) == expected


@pytest.mark.parametrize("url", INVALID_URLS)
def test_parse_run_url_invalid(url: str) -> None:
    with pytest.raises(ValueError, match="GitHub Actions run URL") as excinfo:
        parse_run_url(url)
    assert repr(url) in str(excinfo.value)
