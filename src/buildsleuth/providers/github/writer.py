"""Open a draft pull request from a verified patch.

The only module in the project that writes to a repository. It refuses unless
the target is on the allowlist and the patch passes policy, and it labels and
describes what it opens so nobody has to guess where the change came from.
"""

import base64
from dataclasses import dataclass

from buildsleuth.providers.github.client import GitHubClient

AI_LABEL = "ai-generated"
BRANCH_PREFIX = "buildsleuth/fix"
MAX_EVIDENCE_LINES = 3
MAX_EVIDENCE_CHARS = 300
NO_EVIDENCE = "none recorded"
BLOB_ENCODING = "base64"
FILE_MODE = "100644"
BLOB_TYPE = "blob"


@dataclass(frozen=True)
class DraftPullRequest:
    number: int
    url: str
    branch: str


def branch_name(run_id: int) -> str:
    return f"{BRANCH_PREFIX}-{run_id}"


def quote_evidence(lines: list[str]) -> str:
    """Render log lines as a blockquote that their content cannot escape.

    Evidence is model output quoted from a CI log, and on a pull request from
    a fork the log content is written by whoever opened it. Left raw, a
    newline breaks out of the blockquote and unbalanced backticks swallow the
    text below, including the line saying a machine wrote this patch. So each
    line is flattened, fenced characters are neutralised, and the length is
    capped.
    """
    if not lines:
        return f"> {NO_EVIDENCE}"

    rendered: list[str] = []
    for raw in lines[:MAX_EVIDENCE_LINES]:
        flattened = " ".join(raw.replace("\r", "\n").split("\n"))
        safe = flattened.replace("`", "'").strip()[:MAX_EVIDENCE_CHARS]
        rendered.append(f"> {safe}" if safe else f"> {NO_EVIDENCE}")
    return "\n".join(rendered)


def build_pr_body(
    failure_class: str,
    subcategory: str,
    strategy: str,
    expected_effect: str,
    evidence: list[str],
    verification: str,
    run_url: str,
    model: str,
) -> str:
    """The body a reviewer reads.

    States what was changed, what was checked, and what was not, because a
    patch whose provenance is unclear costs more to review than it saves.
    """
    quoted = quote_evidence(evidence)
    return "\n".join(
        [
            "## Automated triage",
            "",
            f"This draft was opened by [BuildSleuth](https://github.com/rajeev-chaurasia/build-sleuth)"
            f" from [a failing run]({run_url}). It was written by `{model}`, not by a person.",
            "",
            f"**Classified as:** `{failure_class}` / `{subcategory}`",
            "",
            f"**Change:** {strategy}",
            "",
            f"**Expected effect:** {expected_effect}",
            "",
            "**Evidence from the log:**",
            "",
            quoted,
            "",
            "**Verification:**",
            "",
            verification,
            "",
            "---",
            "",
            "Review this as you would any other patch. The checks above say what was"
            " confirmed mechanically; they do not say the change is the right one.",
        ]
    )


class GitHubWriter:
    """Creates a branch, a commit and a draft pull request. Nothing else."""

    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    def create_branch_commit(
        self, repo: str, base_sha: str, branch: str, files: dict[str, str], message: str
    ) -> str:
        """Commit changed files onto a new branch in one atomic commit."""
        tree_entries = []
        for path, content in files.items():
            blob = self._client.create_blob(
                repo, base64.b64encode(content.encode("utf-8")).decode("ascii"), BLOB_ENCODING
            )
            tree_entries.append(
                {
                    "path": path,
                    "mode": FILE_MODE,
                    "type": BLOB_TYPE,
                    "sha": str(blob["sha"]),
                }
            )

        tree = self._client.create_tree(repo, base_sha, tree_entries)
        commit = self._client.create_commit(repo, message, str(tree["sha"]), [base_sha])
        commit_sha = str(commit["sha"])
        self._client.create_ref(repo, f"refs/heads/{branch}", commit_sha)
        return commit_sha

    def open_draft_pr(
        self, repo: str, branch: str, base: str, title: str, body: str
    ) -> DraftPullRequest:
        """Open the pull request as a draft and label it as machine written."""
        payload = self._client.create_pull_request(repo, title, branch, base, body, draft=True)
        number = int(payload["number"])
        self._client.add_labels(repo, number, [AI_LABEL])
        return DraftPullRequest(number=number, url=str(payload["html_url"]), branch=branch)
