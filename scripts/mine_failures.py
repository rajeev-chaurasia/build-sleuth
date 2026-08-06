"""Find failing runs across public repositories and snapshot them.

Run as `uv run python scripts/mine_failures.py --limit 40`.

Snapshots are taken immediately rather than lazily, because GitHub deletes
workflow logs after ninety days and a benchmark whose evidence expires is not
a benchmark.
"""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from buildsleuth.config import load_settings
from buildsleuth.dataset.mining import Candidate, build_candidate, diversify
from buildsleuth.models.run import Conclusion, RunRef
from buildsleuth.pipeline.ingest import NoFailedJobError, pick_failed_job
from buildsleuth.providers.github.client import GitHubApiError, GitHubClient
from buildsleuth.providers.github.provider import GitHubProvider

# Chosen for CI volume, ecosystem spread, and because all of them run their
# pull request checks on GitHub Actions rather than an external service.
DEFAULT_REPOS = (
    "apache/airflow",
    "home-assistant/core",
    "pandas-dev/pandas",
    "scikit-learn/scikit-learn",
    "PrefectHQ/prefect",
    "huggingface/transformers",
    "pola-rs/polars",
    "astral-sh/uv",
    "encode/httpx",
    "pydantic/pydantic",
    "fastapi/fastapi",
    "psf/black",
)
DEFAULT_PER_REPO = 4
DEFAULT_LIMIT = 40
RUNS_PER_PAGE = 20
CANDIDATES_FILE = "candidates.json"


def collect(provider: GitHubProvider, client: GitHubClient, repos: list[str]) -> list[Candidate]:
    """Walk each repo's recent failures and describe what the signals say."""
    found: list[Candidate] = []
    for repo in repos:
        try:
            runs = client.list_failed_runs(repo, RUNS_PER_PAGE, event="pull_request")
        except GitHubApiError as error:
            print(f"  {repo}: skipped, {error}")
            continue

        for payload in runs:
            run_id = int(payload["id"])  # type: ignore[call-overload]
            ref = RunRef(repo=repo, run_id=run_id)
            try:
                run = provider.get_run(ref)
                jobs = provider.get_jobs(ref)
                job = pick_failed_job(jobs)
            except (GitHubApiError, NoFailedJobError):
                continue

            if run.conclusion is not Conclusion.FAILURE and job.conclusion is None:
                continue
            found.append(build_candidate(run, job))
        print(f"  {repo}: {sum(1 for c in found if c.repo == repo)} candidates")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repos", default=",".join(DEFAULT_REPOS))
    parser.add_argument("--per-repo", type=int, default=DEFAULT_PER_REPO)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--out", type=Path, default=Path("mined"))
    args = parser.parse_args()

    settings = load_settings()
    token = settings.github_token.get_secret_value() if settings.github_token else None
    if not token:
        raise SystemExit("mining needs BUILDSLEUTH_GITHUB_TOKEN, the anonymous limit is 60/hour")

    repos = [name.strip() for name in args.repos.split(",") if name.strip()]
    client = GitHubClient(token=token)
    try:
        provider = GitHubProvider(client)
        print(f"scanning {len(repos)} repositories")
        candidates = list(diversify(collect(provider, client, repos), args.per_repo))[: args.limit]
    finally:
        client.close()

    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / CANDIDATES_FILE
    path.write_text(
        json.dumps([asdict(candidate) for candidate in candidates], indent=2), encoding="utf-8"
    )

    by_signal: dict[str, int] = {}
    for candidate in candidates:
        by_signal[candidate.signal.value] = by_signal.get(candidate.signal.value, 0) + 1
    print(f"\n{len(candidates)} candidates written to {path}")
    for signal, count in sorted(by_signal.items()):
        print(f"  {signal}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
