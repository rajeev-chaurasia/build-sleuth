"""Download the evidence for mined candidates before GitHub deletes it.

Run as `uv run python scripts/snapshot_candidates.py`.

Writes one directory per candidate holding the cleaned failing log, the diff
when the run has an associated pull request, and the run metadata. Labels are
not written: that is a separate, human-supervised step.
"""

import argparse
import json
from pathlib import Path

from buildsleuth.config import load_settings
from buildsleuth.pipeline.ingest import ingest
from buildsleuth.providers.github.client import GitHubApiError, GitHubClient
from buildsleuth.providers.github.provider import GitHubProvider

CANDIDATES_FILE = "candidates.json"
METADATA_FILE = "metadata.json"
LOG_FILE = "failed_job.txt"
DIFF_FILE = "diff.patch"
MIN_USEFUL_LOG_CHARS = 200


def _run_url(repo: str, run_id: int) -> str:
    return f"https://github.com/{repo}/actions/runs/{run_id}"


def snapshot_one(provider: GitHubProvider, out_dir: Path, candidate: dict[str, object]) -> str:
    """Fetch one candidate. Returns a one-line status for the console."""
    repo = str(candidate["repo"])
    run_id = int(candidate["run_id"])  # type: ignore[call-overload]
    slug = f"{repo.replace('/', '-')}-{run_id}"
    target = out_dir / slug

    if (target / LOG_FILE).exists():
        return f"  {slug}: already snapshotted"

    try:
        snapshot = ingest(_run_url(repo, run_id), provider)
    except (GitHubApiError, Exception) as error:
        return f"  {slug}: skipped, {type(error).__name__}: {error}"

    if len(snapshot.log_text) < MIN_USEFUL_LOG_CHARS:
        return f"  {slug}: skipped, log too short to judge"

    target.mkdir(parents=True, exist_ok=True)
    (target / LOG_FILE).write_text(snapshot.log_text, encoding="utf-8")
    if snapshot.diff_text:
        (target / DIFF_FILE).write_text(snapshot.diff_text, encoding="utf-8")

    metadata = {
        "repo": repo,
        "run_id": run_id,
        "run_attempt": snapshot.run.run_attempt,
        "head_sha": snapshot.run.head_sha,
        "workflow_name": snapshot.run.workflow_name,
        "failed_job": snapshot.failed_job.name,
        "failed_step": snapshot.failed_step.name if snapshot.failed_step else None,
        "pr_number": snapshot.pr_number,
        "has_diff": bool(snapshot.diff_text),
        "log_lines": snapshot.log_text.count("\n") + 1,
        "original_url": _run_url(repo, run_id),
        "mining_signal": candidate.get("signal"),
    }
    (target / METADATA_FILE).write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    diff_note = "with diff" if snapshot.diff_text else "no diff"
    return f"  {slug}: {metadata['log_lines']} lines, {diff_note}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mined", type=Path, default=Path("mined"))
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()

    candidates = json.loads((args.mined / CANDIDATES_FILE).read_text(encoding="utf-8"))
    settings = load_settings()
    token = settings.github_token.get_secret_value() if settings.github_token else None
    if not token:
        raise SystemExit("snapshotting needs BUILDSLEUTH_GITHUB_TOKEN")

    client = GitHubClient(token=token)
    try:
        provider = GitHubProvider(client)
        for candidate in candidates[: args.limit]:
            print(snapshot_one(provider, args.mined, candidate), flush=True)
    finally:
        client.close()

    snapshotted = sum(1 for path in args.mined.iterdir() if (path / LOG_FILE).exists())
    print(f"\n{snapshotted} candidates have evidence on disk under {args.mined}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
