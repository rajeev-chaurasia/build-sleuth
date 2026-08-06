"""Provider protocols. Read side and write side are deliberately separate.

The read side (CIProvider) is broad and safe. The write side (RepoWriter,
added in the fix-PR phase) stays tiny and is always called through guardrails.
"""

from typing import Protocol

from buildsleuth.models.run import Job, RunRef, WorkflowRun


class CIProvider(Protocol):
    def parse_run_url(self, url: str) -> RunRef: ...

    def get_run(self, ref: RunRef) -> WorkflowRun: ...

    def get_jobs(self, ref: RunRef) -> list[Job]: ...

    def get_job_log(self, ref: RunRef, job: Job) -> str: ...

    def get_attempt_log(self, ref: RunRef, attempt: int, job_name: str) -> str | None: ...

    def get_pr_for_commit(self, repo: str, sha: str) -> int | None: ...

    def get_diff(self, repo: str, pr_number: int) -> str: ...

    def get_file(self, repo: str, path: str, ref: str) -> str | None: ...
