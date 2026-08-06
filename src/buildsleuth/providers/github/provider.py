"""GitHub implementation of the CIProvider read-side protocol."""

from typing import Any

from buildsleuth.models.run import Conclusion, Job, RunRef, Step, WorkflowRun
from buildsleuth.providers.github.client import GitHubClient
from buildsleuth.providers.github.logzip import extract_job_log
from buildsleuth.providers.github.urls import parse_run_url

_PR_STATE_OPEN = "open"


class GitHubProvider:
    """Read-side GitHub Actions provider, composed over GitHubClient."""

    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    def parse_run_url(self, url: str) -> RunRef:
        """Parse a GitHub Actions run URL into a RunRef."""
        return parse_run_url(url)

    def get_run(self, ref: RunRef) -> WorkflowRun:
        """Fetch a workflow run and map it into the domain model."""
        data = self._client.get_run(ref.repo, ref.run_id)
        return WorkflowRun(
            ref=ref,
            workflow_name=data.get("name") or data.get("path") or "",
            run_attempt=data["run_attempt"],
            head_sha=data["head_sha"],
            head_branch=data.get("head_branch"),
            conclusion=_conclusion(data.get("conclusion")),
            event=data["event"],
            html_url=data["html_url"],
        )

    def get_jobs(self, ref: RunRef) -> list[Job]:
        """Fetch all jobs of a run, mapped into the domain model."""
        return [_job(payload) for payload in self._client.get_jobs(ref.repo, ref.run_id)]

    def get_job_log(self, ref: RunRef, job: Job) -> str:
        """Fetch one job's log via the single-job endpoint, preferred over the zip."""
        return self._client.get_job_log(ref.repo, job.job_id)

    def get_attempt_log(self, ref: RunRef, attempt: int, job_name: str) -> str | None:
        """Fetch one job's log for a specific attempt from the attempt logs zip.

        Returns None when the job is not present in the zip.
        """
        zip_bytes = self._client.get_attempt_logs_zip(ref.repo, ref.run_id, attempt)
        return extract_job_log(zip_bytes, job_name)

    def get_pr_for_commit(self, repo: str, sha: str) -> int | None:
        """Return an associated PR number, preferring an open one over a closed one."""
        pulls = self._client.get_pulls_for_commit(repo, sha)
        if not pulls:
            return None
        for pull in pulls:
            if pull.get("state") == _PR_STATE_OPEN:
                return int(pull["number"])
        return int(pulls[0]["number"])

    def get_diff(self, repo: str, pr_number: int) -> str:
        """Fetch a pull request's unified diff, as the branch stands now."""
        return self._client.get_pr_diff(repo, pr_number)

    def get_diff_at(self, repo: str, pr_number: int, head_sha: str) -> str:
        """Diff of a pull request as it stood at one commit.

        Falls back to the current pull request diff when the base cannot be
        resolved, since a slightly stale diff beats no diff at all.
        """
        try:
            base_sha = str(self._client.get_pr(repo, pr_number)["base"]["sha"])
        except (KeyError, TypeError):
            return self.get_diff(repo, pr_number)
        return self._client.get_compare_diff(repo, base_sha, head_sha)

    def get_file(self, repo: str, path: str, ref: str) -> str | None:
        """Fetch a file's content at a ref, or None when it does not exist."""
        return self._client.get_file_content(repo, path, ref)


def _conclusion(raw: str | None) -> Conclusion | None:
    """Map a REST conclusion string, tolerating values outside our enum."""
    if raw is None:
        return None
    try:
        return Conclusion(raw)
    except ValueError:
        return None


def _job(payload: dict[str, Any]) -> Job:
    steps = [_step(step) for step in payload.get("steps") or []]
    return Job(
        job_id=payload["id"],
        name=payload["name"],
        conclusion=_conclusion(payload.get("conclusion")),
        steps=steps,
    )


def _step(payload: dict[str, Any]) -> Step:
    return Step(
        number=payload["number"],
        name=payload["name"],
        conclusion=_conclusion(payload.get("conclusion")),
    )
