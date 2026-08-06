import httpx
import pytest
import respx

from buildsleuth.providers.github.client import GitHubApiError, GitHubClient

API = "https://api.github.com"


@pytest.fixture
def sleep_calls(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    calls: list[float] = []
    monkeypatch.setattr(
        "buildsleuth.providers.github.client.time.sleep",
        lambda seconds: calls.append(seconds),
    )
    return calls


def test_token_sets_bearer_and_api_version_headers(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(f"{API}/repos/o/r/actions/runs/1").respond(json={"id": 1})
    GitHubClient(token="tok123").get_run("o/r", 1)
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer tok123"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"


def test_no_token_sends_no_authorization_header(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(f"{API}/repos/o/r/actions/runs/1").respond(json={"id": 1})
    GitHubClient(token=None).get_run("o/r", 1)
    assert "Authorization" not in route.calls.last.request.headers


def test_custom_base_url_is_used(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get("https://ghe.example.com/api/v3/repos/o/r/actions/runs/1").respond(
        json={"id": 1}
    )
    client = GitHubClient(token=None, base_url="https://ghe.example.com/api/v3")
    assert client.get_run("o/r", 1) == {"id": 1}
    assert route.called


def test_get_run_returns_parsed_json(respx_mock: respx.MockRouter) -> None:
    payload = {"id": 1, "name": "CI", "conclusion": "failure"}
    respx_mock.get(f"{API}/repos/o/r/actions/runs/1").respond(json=payload)
    assert GitHubClient(token=None).get_run("o/r", 1) == payload


def test_get_jobs_paginates_via_link_header(respx_mock: respx.MockRouter) -> None:
    jobs_url = f"{API}/repos/o/r/actions/runs/1/jobs"
    route = respx_mock.get(jobs_url).mock(
        side_effect=[
            httpx.Response(
                200,
                json={"jobs": [{"id": 1}]},
                headers={"Link": f'<{jobs_url}?per_page=100&page=2>; rel="next"'},
            ),
            httpx.Response(200, json={"jobs": [{"id": 2}]}),
        ]
    )
    jobs = GitHubClient(token=None).get_jobs("o/r", 1)
    assert [job["id"] for job in jobs] == [1, 2]
    assert route.call_count == 2
    assert route.calls[0].request.url.params["per_page"] == "100"
    assert route.calls[1].request.url.params["page"] == "2"


def test_get_job_log_follows_302_redirect(respx_mock: respx.MockRouter) -> None:
    signed_url = "https://signed.example.com/logs/abc"
    respx_mock.get(f"{API}/repos/o/r/actions/jobs/9/logs").respond(
        302, headers={"Location": signed_url}
    )
    signed = respx_mock.get(signed_url).respond(200, text="line one\nline two")
    assert GitHubClient(token="tok").get_job_log("o/r", 9) == "line one\nline two"
    assert signed.called
    assert "Authorization" not in signed.calls.last.request.headers


def test_get_run_logs_zip_follows_302_and_returns_bytes(respx_mock: respx.MockRouter) -> None:
    signed_url = "https://signed.example.com/run.zip"
    respx_mock.get(f"{API}/repos/o/r/actions/runs/1/logs").respond(
        302, headers={"Location": signed_url}
    )
    respx_mock.get(signed_url).respond(200, content=b"PK\x03\x04data")
    assert GitHubClient(token=None).get_run_logs_zip("o/r", 1) == b"PK\x03\x04data"


def test_get_attempt_logs_zip_hits_attempt_endpoint(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(f"{API}/repos/o/r/actions/runs/1/attempts/2/logs").respond(
        200, content=b"zipbytes"
    )
    assert GitHubClient(token=None).get_attempt_logs_zip("o/r", 1, 2) == b"zipbytes"
    assert route.called


def test_get_pulls_for_commit_returns_list(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/repos/o/r/commits/abc123/pulls").respond(
        json=[{"number": 12}, {"number": 34}]
    )
    assert GitHubClient(token=None).get_pulls_for_commit("o/r", "abc123") == [
        {"number": 12},
        {"number": 34},
    ]


def test_get_pr_diff_sends_diff_accept_header(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(f"{API}/repos/o/r/pulls/12").respond(text="diff --git a/x b/x\n")
    assert GitHubClient(token=None).get_pr_diff("o/r", 12) == "diff --git a/x b/x\n"
    assert route.calls.last.request.headers["Accept"] == "application/vnd.github.diff"


def test_get_file_content_sends_ref_and_raw_accept(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(
        f"{API}/repos/o/r/contents/.github/workflows/ci.yml", params={"ref": "abc123"}
    ).respond(text="name: ci\n")
    content = GitHubClient(token=None).get_file_content("o/r", ".github/workflows/ci.yml", "abc123")
    assert content == "name: ci\n"
    assert route.calls.last.request.headers["Accept"] == "application/vnd.github.raw+json"


def test_get_file_content_returns_none_on_404(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/repos/o/r/contents/missing.txt").respond(
        404, json={"message": "Not Found"}
    )
    assert GitHubClient(token=None).get_file_content("o/r", "missing.txt", "main") is None


def test_retry_after_honored_on_429(respx_mock: respx.MockRouter, sleep_calls: list[float]) -> None:
    route = respx_mock.get(f"{API}/repos/o/r/actions/runs/1").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={"id": 1}),
        ]
    )
    assert GitHubClient(token=None).get_run("o/r", 1) == {"id": 1}
    assert sleep_calls == [7.0]
    assert route.call_count == 2


def test_retry_after_honored_on_403(respx_mock: respx.MockRouter, sleep_calls: list[float]) -> None:
    respx_mock.get(f"{API}/repos/o/r/actions/runs/1").mock(
        side_effect=[
            httpx.Response(403, headers={"Retry-After": "1"}),
            httpx.Response(200, json={"id": 1}),
        ]
    )
    assert GitHubClient(token=None).get_run("o/r", 1) == {"id": 1}
    assert sleep_calls == [1.0]


def test_retry_sleep_capped_at_30_seconds(
    respx_mock: respx.MockRouter, sleep_calls: list[float]
) -> None:
    respx_mock.get(f"{API}/repos/o/r/actions/runs/1").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "120"}),
            httpx.Response(200, json={"id": 1}),
        ]
    )
    assert GitHubClient(token=None).get_run("o/r", 1) == {"id": 1}
    assert sleep_calls == [30.0]


def test_retries_stop_after_two_attempts(
    respx_mock: respx.MockRouter, sleep_calls: list[float]
) -> None:
    route = respx_mock.get(f"{API}/repos/o/r/actions/runs/1").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "1"})] * 3
    )
    with pytest.raises(GitHubApiError) as excinfo:
        GitHubClient(token=None).get_run("o/r", 1)
    assert excinfo.value.status == 429
    assert route.call_count == 3
    assert sleep_calls == [1.0, 1.0]


def test_403_without_retry_after_raises_immediately(
    respx_mock: respx.MockRouter, sleep_calls: list[float]
) -> None:
    route = respx_mock.get(f"{API}/repos/o/r/actions/runs/1").respond(
        403, json={"message": "rate limited"}
    )
    with pytest.raises(GitHubApiError) as excinfo:
        GitHubClient(token=None).get_run("o/r", 1)
    assert excinfo.value.status == 403
    assert "rate limited" in str(excinfo.value)
    assert route.call_count == 1
    assert sleep_calls == []


def test_api_error_carries_status_message_and_url(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/repos/o/r/actions/runs/1").respond(500, json={"message": "boom"})
    with pytest.raises(GitHubApiError) as excinfo:
        GitHubClient(token=None).get_run("o/r", 1)
    error = excinfo.value
    assert error.status == 500
    assert error.message == "boom"
    assert error.url.endswith("/repos/o/r/actions/runs/1")
    assert "boom" in str(error)


def test_api_error_uses_body_text_when_not_json(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/repos/o/r/actions/runs/1").respond(502, text="bad gateway")
    with pytest.raises(GitHubApiError) as excinfo:
        GitHubClient(token=None).get_run("o/r", 1)
    assert excinfo.value.message == "bad gateway"
