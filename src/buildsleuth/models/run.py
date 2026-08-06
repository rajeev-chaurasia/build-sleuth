"""Models for a CI run and its offline snapshot.

RunSnapshot is the boundary between the online world (provider APIs) and
everything downstream. Once a snapshot exists, triage runs fully offline,
which is what makes eval cases replayable.
"""

from enum import StrEnum

from pydantic import BaseModel


class Conclusion(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    SKIPPED = "skipped"
    NEUTRAL = "neutral"
    ACTION_REQUIRED = "action_required"


class RunRef(BaseModel):
    """Provider-independent pointer to one workflow run."""

    repo: str  # "owner/name"
    run_id: int
    attempt: int | None = None


class Step(BaseModel):
    number: int
    name: str
    conclusion: Conclusion | None = None


class Job(BaseModel):
    job_id: int
    name: str
    conclusion: Conclusion | None = None
    steps: list[Step] = []

    def failed_steps(self) -> list[Step]:
        return [s for s in self.steps if s.conclusion == Conclusion.FAILURE]


class WorkflowRun(BaseModel):
    ref: RunRef
    workflow_name: str
    run_attempt: int
    head_sha: str
    head_branch: str | None = None
    conclusion: Conclusion | None = None
    event: str
    html_url: str


class RunSnapshot(BaseModel):
    """Everything triage needs, captured once, usable offline forever."""

    run: WorkflowRun
    failed_job: Job
    failed_step: Step | None = None
    log_text: str  # cleaned log of the failed job
    prior_attempt_log: str | None = None  # failed first attempt when the run was re-run
    pr_number: int | None = None
    diff_text: str | None = None
    workflow_yaml: str | None = None
