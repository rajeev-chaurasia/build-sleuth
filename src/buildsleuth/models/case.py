"""Dataset schema for one curated CI failure case.

A case is self-contained: inputs point at snapshotted files inside the case
directory, so the benchmark stays runnable after GitHub expires the original
logs (90 day default retention).
"""

from datetime import date
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, HttpUrl, model_validator

from buildsleuth.models.taxonomy import FailureClass, is_valid_subcategory

SMOKE_TAG = "smoke"


class CaseSource(StrEnum):
    GHA_MINED = "gha_mined"
    BUGSWARM = "bugswarm"
    LOGDX_CI = "logdx_ci"
    SYNTHETIC = "synthetic"


class LabelingMethod(StrEnum):
    """How a case's ground truth was derived, weakest evidence last.

    LOG_EVIDENCE means the label was read off the failure log alone, with no
    corroborating rerun, fix commit, or maintainer issue. It is the weakest
    method and is tracked separately so headline metrics can be split by it.
    """

    RERUN_ATTEMPT_HEURISTIC = "rerun_attempt_heuristic"
    FIX_COMMIT_HEURISTIC = "fix_commit_heuristic"
    ISSUE_CROSSREF = "issue_crossref"
    IMPORTED = "imported"
    CONSTRUCTED = "constructed"
    LOG_EVIDENCE = "log_evidence"


class VerificationMethod(StrEnum):
    PYTEST_DOCKER = "pytest_docker"
    BUGSWARM_IMAGE = "bugswarm_image"
    NONE = "none"


class CaseInputs(BaseModel):
    provider: Literal["github_actions"] = "github_actions"
    repo: str
    run_id: int
    run_attempt: int = 1
    head_sha: str
    pr_number: int | None = None
    failed_job_name: str
    failed_step_name: str | None = None
    log_files: list[str]  # paths relative to the case directory
    diff_file: str | None = None
    workflow_file: str | None = None
    passing_run_log: str | None = None


class GroundTruth(BaseModel):
    failure_class: FailureClass
    subcategory: str
    related_to_diff: bool
    culprit_files: list[str] = []
    failing_tests: list[str] = []
    fix_commit_sha: str | None = None
    fix_summary: str | None = None

    @model_validator(mode="after")
    def check_subcategory(self) -> Self:
        if not is_valid_subcategory(self.failure_class, self.subcategory):
            raise ValueError(
                f"subcategory {self.subcategory!r} is not valid for {self.failure_class}"
            )
        return self


class Provenance(BaseModel):
    source: CaseSource
    labeling_method: LabelingMethod
    verified_by_human: bool = False
    verified_date: date | None = None
    original_url: HttpUrl | None = None
    snapshot_date: date
    notes: str = ""


class Verification(BaseModel):
    method: VerificationMethod = VerificationMethod.NONE
    docker_image: str | None = None
    setup_commands: list[str] = []
    test_command: str | None = None
    no_new_breaks_command: str | None = None
    timeout_seconds: int = 600

    @model_validator(mode="after")
    def check_runnable(self) -> Self:
        if self.method != VerificationMethod.NONE and not self.test_command:
            raise ValueError(f"verification method {self.method} requires a test_command")
        return self


class TriageCase(BaseModel):
    schema_version: int = 1
    case_id: str
    title: str
    inputs: CaseInputs
    ground_truth: GroundTruth
    provenance: Provenance
    verification: Verification = Verification()
    tags: list[str] = []

    @property
    def is_smoke(self) -> bool:
        return SMOKE_TAG in self.tags

    @model_validator(mode="after")
    def check_smoke_cases_are_verified(self) -> Self:
        """Heuristic labels are fine for the full suite but never for the PR gate."""
        if self.is_smoke and not self.provenance.verified_by_human:
            raise ValueError(f"smoke case {self.case_id} must have verified_by_human set")
        return self
