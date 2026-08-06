"""Find failing runs worth curating, and say what the evidence suggests.

Nothing here decides a label. It proposes one from signals that are cheap and
checkable, records which signal fired, and leaves the judgement to a person or
an adjudicator. A heuristic that quietly became ground truth would put noise
straight into the benchmark.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

from buildsleuth.models.run import Conclusion, Job, WorkflowRun
from buildsleuth.models.taxonomy import FailureClass

FIRST_ATTEMPT = 1

# Steps that run before the project's own code does. A failure here is the
# environment failing to be built, not the code failing to work.
SETUP_STEP_MARKERS = (
    "checkout",
    "set up ",
    "setup-",
    "install",
    "restore cache",
    "docker pull",
    "login",
)


class Signal(StrEnum):
    """Why a candidate looks like the class it was proposed as."""

    RERUN_PASSED_SAME_COMMIT = "rerun_passed_same_commit"
    CANCELLED_OR_TIMED_OUT = "cancelled_or_timed_out"
    FAILED_IN_SETUP_STEP = "failed_in_setup_step"
    FAILED_IN_PROJECT_STEP = "failed_in_project_step"
    NO_SIGNAL = "no_signal"


# What each signal suggests, and how far to trust it. Nothing above medium:
# these are hints for a labeller, not answers.
_SIGNAL_MEANING: dict[Signal, tuple[FailureClass | None, str]] = {
    Signal.RERUN_PASSED_SAME_COMMIT: (
        FailureClass.FLAKY_TEST,
        "a later attempt on the same commit passed with no code change",
    ),
    Signal.CANCELLED_OR_TIMED_OUT: (
        FailureClass.INFRA_ENVIRONMENT,
        "the job was cancelled or timed out rather than reporting a failure",
    ),
    Signal.FAILED_IN_SETUP_STEP: (
        FailureClass.INFRA_ENVIRONMENT,
        "the failing step runs before the project's own code",
    ),
    Signal.FAILED_IN_PROJECT_STEP: (
        FailureClass.CODE_CHANGE,
        "the failing step runs the project's own build or tests",
    ),
    Signal.NO_SIGNAL: (None, "no cheap signal applied, so the log has to decide"),
}


@dataclass(frozen=True)
class Candidate:
    """One failing run and what the cheap signals say about it."""

    repo: str
    run_id: int
    run_attempt: int
    head_sha: str
    workflow_name: str
    failed_job: str
    failed_step: str | None
    html_url: str
    signal: Signal

    @property
    def suggested_class(self) -> FailureClass | None:
        return _SIGNAL_MEANING[self.signal][0]

    @property
    def rationale(self) -> str:
        return _SIGNAL_MEANING[self.signal][1]


def is_setup_step(step_name: str) -> bool:
    lowered = step_name.lower()
    return any(marker in lowered for marker in SETUP_STEP_MARKERS)


def classify_signal(run: WorkflowRun, job: Job, passed_on_rerun: bool = False) -> Signal:
    """Pick the strongest cheap signal available for this run."""
    if passed_on_rerun and run.run_attempt > FIRST_ATTEMPT:
        return Signal.RERUN_PASSED_SAME_COMMIT
    if job.conclusion in (Conclusion.CANCELLED, Conclusion.TIMED_OUT):
        return Signal.CANCELLED_OR_TIMED_OUT

    failed = job.failed_steps()
    if not failed:
        return Signal.NO_SIGNAL
    return (
        Signal.FAILED_IN_SETUP_STEP
        if is_setup_step(failed[0].name)
        else (Signal.FAILED_IN_PROJECT_STEP)
    )


def build_candidate(run: WorkflowRun, job: Job, passed_on_rerun: bool = False) -> Candidate:
    failed = job.failed_steps()
    return Candidate(
        repo=run.ref.repo,
        run_id=run.ref.run_id,
        run_attempt=run.run_attempt,
        head_sha=run.head_sha,
        workflow_name=run.workflow_name,
        failed_job=job.name,
        failed_step=failed[0].name if failed else None,
        html_url=run.html_url,
        signal=classify_signal(run, job, passed_on_rerun),
    )


def diversify(candidates: list[Candidate], per_repo: int) -> Iterator[Candidate]:
    """Cap how many cases any one repository contributes.

    A benchmark that is mostly one project measures that project, so the cap
    matters more than the raw count.
    """
    seen: dict[str, int] = {}
    for candidate in candidates:
        count = seen.get(candidate.repo, 0)
        if count >= per_repo:
            continue
        seen[candidate.repo] = count + 1
        yield candidate
