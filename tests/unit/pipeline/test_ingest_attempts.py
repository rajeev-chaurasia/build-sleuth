"""Which attempt gets triaged, and which one becomes the flake evidence.

Both behaviours were wrong until a reviewer noticed, and both are invisible
unless a run has been rerun more than once.
"""

from buildsleuth.models.run import Conclusion, Job, RunRef, Step, WorkflowRun
from buildsleuth.pipeline.ingest import ingest

REPO = "octo/demo"
RUN_ID = 77
RAW_LOG = "2026-08-05T10:00:00.0000000Z FAILED tests/test_x.py::test_y\n"


class RecordingProvider:
    """Records which attempts were asked for."""

    def __init__(self, run_attempt: int) -> None:
        self.run_attempt = run_attempt
        self.run_refs: list[RunRef] = []
        self.attempts_requested: list[int] = []

    def parse_run_url(self, url: str) -> RunRef:
        attempt = int(url.rsplit("/", 1)[-1]) if "/attempts/" in url else None
        return RunRef(repo=REPO, run_id=RUN_ID, attempt=attempt)

    def get_run(self, ref: RunRef) -> WorkflowRun:
        self.run_refs.append(ref)
        return WorkflowRun(
            ref=ref,
            workflow_name="CI",
            run_attempt=self.run_attempt,
            head_sha="abc123",
            conclusion=Conclusion.FAILURE,
            event="pull_request",
            html_url=f"https://github.com/{REPO}/actions/runs/{RUN_ID}",
        )

    def get_jobs(self, ref: RunRef) -> list[Job]:
        return [
            Job(
                job_id=1,
                name="checks",
                conclusion=Conclusion.FAILURE,
                steps=[Step(number=1, name="Tests", conclusion=Conclusion.FAILURE)],
            )
        ]

    def get_job_log(self, ref: RunRef, job: Job) -> str:
        return RAW_LOG

    def get_attempt_log(self, ref: RunRef, attempt: int, job_name: str) -> str | None:
        self.attempts_requested.append(attempt)
        return RAW_LOG

    def get_pr_for_commit(self, repo: str, sha: str) -> int | None:
        return None

    def get_diff(self, repo: str, pr_number: int) -> str:
        return ""

    def get_file(self, repo: str, path: str, ref: str) -> str | None:
        return None


def _url(attempt: int | None = None) -> str:
    base = f"https://github.com/{REPO}/actions/runs/{RUN_ID}"
    return f"{base}/attempts/{attempt}" if attempt else base


def test_flake_evidence_comes_from_the_attempt_immediately_before() -> None:
    """On a third attempt, attempt one is two reruns of history away."""
    provider = RecordingProvider(run_attempt=3)
    ingest(_url(), provider)

    assert provider.attempts_requested == [2]


def test_on_a_second_attempt_the_previous_one_is_the_first() -> None:
    provider = RecordingProvider(run_attempt=2)
    ingest(_url(), provider)

    assert provider.attempts_requested == [1]


def test_a_first_attempt_has_no_previous_one_to_fetch() -> None:
    provider = RecordingProvider(run_attempt=1)
    snapshot = ingest(_url(), provider)

    assert provider.attempts_requested == []
    assert snapshot.prior_attempt_log is None


def test_a_url_naming_an_attempt_triages_that_attempt() -> None:
    """The plain run endpoint answers with the latest, so the ref must carry it."""
    provider = RecordingProvider(run_attempt=2)
    ingest(_url(attempt=2), provider)

    assert provider.run_refs[0].attempt == 2


def test_a_url_without_an_attempt_leaves_it_unset() -> None:
    provider = RecordingProvider(run_attempt=3)
    ingest(_url(), provider)

    assert provider.run_refs[0].attempt is None
