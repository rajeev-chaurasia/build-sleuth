import io
import zipfile

import respx

from buildsleuth.models.run import Conclusion, Job, RunRef
from buildsleuth.providers.github.client import GitHubClient
from buildsleuth.providers.github.provider import GitHubProvider

API = "https://api.github.com"
REF = RunRef(repo="o/r", run_id=42)

RUN_PAYLOAD = {
    "id": 42,
    "name": "CI",
    "run_attempt": 2,
    "head_sha": "abc123",
    "head_branch": "main",
    "conclusion": "failure",
    "event": "pull_request",
    "html_url": "https://github.com/o/r/actions/runs/42",
}

JOBS_PAYLOAD = {
    "jobs": [
        {
            "id": 7,
            "name": "build",
            "conclusion": "failure",
            "steps": [
                {"number": 1, "name": "Set up job", "conclusion": "success"},
                {"number": 2, "name": "Run tests", "conclusion": "failure"},
            ],
        },
        {"id": 8, "name": "lint", "conclusion": None, "steps": None},
    ]
}


def make_provider() -> GitHubProvider:
    return GitHubProvider(GitHubClient(token=None))


def make_zip(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in entries.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def test_parse_run_url_delegates() -> None:
    ref = make_provider().parse_run_url("https://github.com/o/r/actions/runs/42/attempts/2")
    assert ref == RunRef(repo="o/r", run_id=42, attempt=2)


def test_get_run_maps_payload(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/repos/o/r/actions/runs/42").respond(json=RUN_PAYLOAD)
    run = make_provider().get_run(REF)
    assert run.ref == REF
    assert run.workflow_name == "CI"
    assert run.run_attempt == 2
    assert run.head_sha == "abc123"
    assert run.head_branch == "main"
    assert run.conclusion == Conclusion.FAILURE
    assert run.event == "pull_request"
    assert run.html_url == "https://github.com/o/r/actions/runs/42"


def test_get_run_tolerates_unknown_conclusion(respx_mock: respx.MockRouter) -> None:
    payload = {**RUN_PAYLOAD, "conclusion": "stale", "head_branch": None}
    respx_mock.get(f"{API}/repos/o/r/actions/runs/42").respond(json=payload)
    run = make_provider().get_run(REF)
    assert run.conclusion is None
    assert run.head_branch is None


def test_get_jobs_maps_jobs_and_steps(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/repos/o/r/actions/runs/42/jobs").respond(json=JOBS_PAYLOAD)
    jobs = make_provider().get_jobs(REF)
    assert [job.job_id for job in jobs] == [7, 8]
    build = jobs[0]
    assert build.name == "build"
    assert build.conclusion == Conclusion.FAILURE
    assert [step.number for step in build.steps] == [1, 2]
    assert [step.name for step in build.failed_steps()] == ["Run tests"]
    lint = jobs[1]
    assert lint.conclusion is None
    assert lint.steps == []


def test_get_job_log_uses_single_job_endpoint(respx_mock: respx.MockRouter) -> None:
    signed_url = "https://signed.example.com/logs/7"
    respx_mock.get(f"{API}/repos/o/r/actions/jobs/7/logs").respond(
        302, headers={"Location": signed_url}
    )
    respx_mock.get(signed_url).respond(200, text="job log body")
    job = Job(job_id=7, name="build")
    assert make_provider().get_job_log(REF, job) == "job log body"


def test_get_attempt_log_extracts_job_from_zip(respx_mock: respx.MockRouter) -> None:
    zip_bytes = make_zip({"1_build.txt": "attempt one log", "build/1_Set up job.txt": "step"})
    respx_mock.get(f"{API}/repos/o/r/actions/runs/42/attempts/1/logs").respond(
        200, content=zip_bytes
    )
    assert make_provider().get_attempt_log(REF, 1, "build") == "attempt one log"


def test_get_attempt_log_returns_none_for_missing_job(respx_mock: respx.MockRouter) -> None:
    zip_bytes = make_zip({"1_build.txt": "attempt one log"})
    respx_mock.get(f"{API}/repos/o/r/actions/runs/42/attempts/1/logs").respond(
        200, content=zip_bytes
    )
    assert make_provider().get_attempt_log(REF, 1, "deploy") is None


def test_get_pr_for_commit_returns_first_number(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/repos/o/r/commits/abc123/pulls").respond(
        json=[{"number": 12}, {"number": 34}]
    )
    assert make_provider().get_pr_for_commit("o/r", "abc123") == 12


def test_get_pr_for_commit_returns_none_when_no_prs(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/repos/o/r/commits/abc123/pulls").respond(json=[])
    assert make_provider().get_pr_for_commit("o/r", "abc123") is None


def test_get_diff_returns_text(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/repos/o/r/pulls/12").respond(text="diff --git a/x b/x\n")
    assert make_provider().get_diff("o/r", 12) == "diff --git a/x b/x\n"


def test_get_file_returns_content(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/repos/o/r/contents/pyproject.toml", params={"ref": "main"}).respond(
        text="[project]\n"
    )
    assert make_provider().get_file("o/r", "pyproject.toml", "main") == "[project]\n"


def test_get_file_returns_none_on_404(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/repos/o/r/contents/nope.txt").respond(404, json={"message": "Not Found"})
    assert make_provider().get_file("o/r", "nope.txt", "main") is None
