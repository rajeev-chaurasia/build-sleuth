"""Ingest stage: job selection, log cleaning, prior attempts, and diff resolution."""

import pytest

from buildsleuth.models.run import Conclusion, Job, RunRef, RunSnapshot, Step, WorkflowRun
from buildsleuth.pipeline.ingest import (
    _TRIAGE_TARGETS,
    FIRST_ATTEMPT,
    NoFailedJobError,
    ingest,
    pick_failed_job,
)

REPO = "octo/demo"
RUN_ID = 42
HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
RUN_URL = f"https://github.com/{REPO}/actions/runs/{RUN_ID}"
JOB_NAME = "build"
PR_NUMBER = 7
SECOND_ATTEMPT = FIRST_ATTEMPT + 1

# One timestamped line, one ANSI-coloured line: both of the things clean_log removes.
RAW_LOG = (
    "2026-01-01T00:00:00.1234567Z \x1b[31mFAILED tests/test_app.py::test_one\x1b[0m\n"
    "2026-01-01T00:00:01.0000000Z AssertionError: 1 != 2\n"
)
CLEAN_LOG = "FAILED tests/test_app.py::test_one\nAssertionError: 1 != 2\n"
RAW_PRIOR_LOG = "2025-12-31T23:00:00Z \x1b[33mFAILED tests/test_app.py::test_one\x1b[0m\n"
CLEAN_PRIOR_LOG = "FAILED tests/test_app.py::test_one\n"

DIFF = "diff --git a/src/app.py b/src/app.py\n"
DIFF_AT_SHA = "diff --git a/src/app.py b/src/app.py\n@@ -1 +1 @@\n"


class FakeProvider:
    """In-memory CIProvider that records the calls ingest makes.

    Deliberately has no get_diff_at, so it also serves as the provider that
    forces the diff fallback.
    """

    def __init__(
        self,
        run: WorkflowRun,
        jobs: list[Job],
        *,
        job_log: str = RAW_LOG,
        attempt_log: str | None = RAW_PRIOR_LOG,
        pr_number: int | None = None,
    ) -> None:
        self._run = run
        self._jobs = jobs
        self._job_log = job_log
        self._attempt_log = attempt_log
        self._pr_number = pr_number
        self.url_calls: list[str] = []
        self.attempt_log_calls: list[tuple[int, str]] = []
        self.diff_calls: list[tuple[str, int]] = []
        self.diff_at_calls: list[tuple[str, int, str]] = []

    def parse_run_url(self, url: str) -> RunRef:
        self.url_calls.append(url)
        return self._run.ref

    def get_run(self, ref: RunRef) -> WorkflowRun:
        return self._run

    def get_jobs(self, ref: RunRef) -> list[Job]:
        return self._jobs

    def get_job_log(self, ref: RunRef, job: Job) -> str:
        return self._job_log

    def get_attempt_log(self, ref: RunRef, attempt: int, job_name: str) -> str | None:
        self.attempt_log_calls.append((attempt, job_name))
        return self._attempt_log

    def get_pr_for_commit(self, repo: str, sha: str) -> int | None:
        return self._pr_number

    def get_diff(self, repo: str, pr_number: int) -> str:
        self.diff_calls.append((repo, pr_number))
        return DIFF

    def get_file(self, repo: str, path: str, ref: str) -> str | None:
        return None


class FakeProviderWithDiffAt(FakeProvider):
    """Provider that offers the commit-pinned diff, which ingest must prefer."""

    def get_diff_at(self, repo: str, pr_number: int, head_sha: str) -> str:
        self.diff_at_calls.append((repo, pr_number, head_sha))
        return DIFF_AT_SHA


def _step(number: int, name: str, conclusion: Conclusion) -> Step:
    return Step(number=number, name=name, conclusion=conclusion)


def _job(
    job_id: int,
    conclusion: Conclusion,
    *,
    name: str = JOB_NAME,
    steps: list[Step] | None = None,
) -> Job:
    return Job(job_id=job_id, name=name, conclusion=conclusion, steps=steps or [])


def _failing_job(steps: list[Step] | None = None) -> Job:
    return _job(
        1,
        Conclusion.FAILURE,
        steps=steps
        if steps is not None
        else [
            _step(1, "Set up job", Conclusion.SUCCESS),
            _step(2, "Run tests", Conclusion.FAILURE),
        ],
    )


def _run(run_attempt: int = FIRST_ATTEMPT) -> WorkflowRun:
    return WorkflowRun(
        ref=RunRef(repo=REPO, run_id=RUN_ID),
        workflow_name="CI",
        run_attempt=run_attempt,
        head_sha=HEAD_SHA,
        head_branch="feature/widget",
        conclusion=Conclusion.FAILURE,
        event="pull_request",
        html_url=RUN_URL,
    )


def test_pick_failed_job_prefers_failure_over_timed_out_and_cancelled() -> None:
    jobs = [
        _job(1, Conclusion.SUCCESS),
        _job(2, Conclusion.CANCELLED),
        _job(3, Conclusion.TIMED_OUT),
        _job(4, Conclusion.FAILURE),
    ]
    assert pick_failed_job(jobs).job_id == 4


@pytest.mark.parametrize("index", range(len(_TRIAGE_TARGETS)))
def test_pick_failed_job_follows_the_declared_preference_order(index: int) -> None:
    """Every target outranks the ones below it, whatever order the jobs arrive in."""
    lower_ranked = list(reversed(_TRIAGE_TARGETS[index:]))
    jobs = [_job(number, target) for number, target in enumerate(lower_ranked)]
    assert pick_failed_job(jobs).conclusion == _TRIAGE_TARGETS[index]


def test_pick_failed_job_picks_timed_out_when_nothing_failed() -> None:
    jobs = [_job(1, Conclusion.SUCCESS), _job(2, Conclusion.TIMED_OUT)]
    assert pick_failed_job(jobs).job_id == 2


def test_pick_failed_job_picks_cancelled_as_a_last_resort() -> None:
    jobs = [_job(1, Conclusion.SKIPPED), _job(2, Conclusion.CANCELLED)]
    assert pick_failed_job(jobs).job_id == 2


def test_pick_failed_job_raises_when_every_job_succeeded() -> None:
    jobs = [_job(1, Conclusion.SUCCESS), _job(2, Conclusion.SKIPPED), _job(3, Conclusion.NEUTRAL)]
    with pytest.raises(NoFailedJobError):
        pick_failed_job(jobs)


def test_pick_failed_job_raises_on_an_empty_job_list() -> None:
    with pytest.raises(NoFailedJobError):
        pick_failed_job([])


def test_ingest_returns_a_snapshot_of_the_failing_job() -> None:
    run = _run()
    failed_job = _failing_job()
    provider = FakeProvider(run, [_job(9, Conclusion.SUCCESS), failed_job], pr_number=PR_NUMBER)

    snapshot = ingest(RUN_URL, provider)

    assert isinstance(snapshot, RunSnapshot)
    assert snapshot.run == run
    assert snapshot.failed_job == failed_job
    assert snapshot.failed_step is not None
    assert snapshot.failed_step.name == "Run tests"
    assert snapshot.pr_number == PR_NUMBER
    assert snapshot.diff_text == DIFF
    assert provider.url_calls == [RUN_URL]


def test_ingest_cleans_the_failing_job_log() -> None:
    """The snapshot is what every later stage reads, so it must hold cleaned text."""
    provider = FakeProvider(_run(), [_failing_job()])

    log_text = ingest(RUN_URL, provider).log_text

    assert log_text == CLEAN_LOG
    assert "2026-01-01T" not in log_text
    assert "\x1b" not in log_text


def test_ingest_takes_the_first_failed_step() -> None:
    steps = [
        _step(1, "Set up job", Conclusion.SUCCESS),
        _step(2, "Run tests", Conclusion.FAILURE),
        _step(3, "Upload coverage", Conclusion.FAILURE),
    ]
    provider = FakeProvider(_run(), [_failing_job(steps)])

    failed_step = ingest(RUN_URL, provider).failed_step

    assert failed_step is not None
    assert failed_step.number == 2


def test_ingest_leaves_the_failed_step_unset_when_no_step_failed() -> None:
    """A job can fail without any step reporting failure, for example on a runner death."""
    steps = [_step(1, "Set up job", Conclusion.SUCCESS)]
    provider = FakeProvider(_run(), [_failing_job(steps)])

    assert ingest(RUN_URL, provider).failed_step is None


def test_ingest_captures_the_cleaned_first_attempt_log_on_a_rerun() -> None:
    """The prior attempt is the evidence that separates a flake from a real break."""
    provider = FakeProvider(_run(SECOND_ATTEMPT), [_failing_job()])

    snapshot = ingest(RUN_URL, provider)

    assert snapshot.prior_attempt_log == CLEAN_PRIOR_LOG
    assert provider.attempt_log_calls == [(FIRST_ATTEMPT, JOB_NAME)]


def test_ingest_tolerates_a_missing_first_attempt_log() -> None:
    """Attempt logs expire before run metadata does, so absence must not be fatal."""
    provider = FakeProvider(_run(SECOND_ATTEMPT), [_failing_job()], attempt_log=None)

    snapshot = ingest(RUN_URL, provider)

    assert snapshot.prior_attempt_log is None
    assert provider.attempt_log_calls == [(FIRST_ATTEMPT, JOB_NAME)]


def test_ingest_does_not_look_for_a_prior_attempt_on_a_first_run() -> None:
    provider = FakeProvider(_run(FIRST_ATTEMPT), [_failing_job()])

    snapshot = ingest(RUN_URL, provider)

    assert snapshot.prior_attempt_log is None
    assert provider.attempt_log_calls == []


def test_ingest_skips_the_diff_when_the_commit_has_no_pull_request() -> None:
    provider = FakeProviderWithDiffAt(_run(), [_failing_job()], pr_number=None)

    snapshot = ingest(RUN_URL, provider)

    assert snapshot.pr_number is None
    assert snapshot.diff_text is None
    assert provider.diff_calls == []
    assert provider.diff_at_calls == []


def test_ingest_prefers_the_diff_pinned_to_the_failing_commit() -> None:
    """A branch-tip diff can describe code the log never ran, so head_sha must be used."""
    provider = FakeProviderWithDiffAt(_run(), [_failing_job()], pr_number=PR_NUMBER)

    snapshot = ingest(RUN_URL, provider)

    assert snapshot.diff_text == DIFF_AT_SHA
    assert provider.diff_at_calls == [(REPO, PR_NUMBER, HEAD_SHA)]
    assert provider.diff_calls == []


def test_ingest_falls_back_to_the_pull_request_diff() -> None:
    """A provider that cannot pin a diff to a commit still gets a usable snapshot."""
    provider = FakeProvider(_run(), [_failing_job()], pr_number=PR_NUMBER)

    snapshot = ingest(RUN_URL, provider)

    assert snapshot.diff_text == DIFF
    assert provider.diff_calls == [(REPO, PR_NUMBER)]


def test_ingest_does_not_capture_the_workflow_file() -> None:
    """Pinned so that fetching workflow yaml later is a deliberate change, not a surprise."""
    provider = FakeProvider(_run(), [_failing_job()])

    assert ingest(RUN_URL, provider).workflow_yaml is None
