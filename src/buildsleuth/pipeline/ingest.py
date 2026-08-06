"""Stage 1: turn a run URL into an offline RunSnapshot.

This is the only stage that talks to the CI provider. Everything after it
works from the snapshot, which is what makes eval cases replayable.
"""

from buildsleuth.condense.clean import clean_log
from buildsleuth.models.run import Conclusion, Job, RunSnapshot
from buildsleuth.providers.base import CIProvider

FIRST_ATTEMPT = 1

# Conclusions that make a job the triage target, in preference order.
_TRIAGE_TARGETS = (Conclusion.FAILURE, Conclusion.TIMED_OUT, Conclusion.CANCELLED)


class NoFailedJobError(Exception):
    """The run has no job in a failed, timed out, or cancelled state."""


def pick_failed_job(jobs: list[Job]) -> Job:
    for wanted in _TRIAGE_TARGETS:
        for job in jobs:
            if job.conclusion == wanted:
                return job
    raise NoFailedJobError("no failed, timed out, or cancelled job in this run")


def ingest(url: str, provider: CIProvider) -> RunSnapshot:
    ref = provider.parse_run_url(url)
    run = provider.get_run(ref)
    jobs = provider.get_jobs(ref)

    failed_job = pick_failed_job(jobs)
    failed_steps = failed_job.failed_steps()
    failed_step = failed_steps[0] if failed_steps else None

    log_text = clean_log(provider.get_job_log(ref, failed_job))

    prior_attempt_log: str | None = None
    if run.run_attempt > FIRST_ATTEMPT:
        raw = provider.get_attempt_log(ref, FIRST_ATTEMPT, failed_job.name)
        prior_attempt_log = clean_log(raw) if raw is not None else None

    pr_number = provider.get_pr_for_commit(ref.repo, run.head_sha)
    diff_text = provider.get_diff(ref.repo, pr_number) if pr_number is not None else None

    return RunSnapshot(
        run=run,
        failed_job=failed_job,
        failed_step=failed_step,
        log_text=log_text,
        prior_attempt_log=prior_attempt_log,
        pr_number=pr_number,
        diff_text=diff_text,
        workflow_yaml=None,
    )
