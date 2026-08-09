"""Choose which BugSwarm artifacts are worth importing, and say why.

Run as `uv run python scripts/select_bugswarm.py --limit 12`.

Prints image tags for `import_bugswarm.py`. Selection is a script rather than
a list of tags so the criteria are inspectable and the same query can be rerun
when the dataset grows.

The criteria exist because most of the catalogue is unsuitable for measuring
fix quality:

- Reproducible at 5/5 stability, because a case that builds intermittently
  measures the weather rather than the patch.
- Classified as a code defect, not a test or build one, so the culprit is a
  source file the fix stage is allowed to edit.
- Exactly one changed file, so localization has an unambiguous answer.
- Named failing tests, so a patch can be checked by rerunning something
  specific rather than by trusting the exit code.

It also caps how many cases one repository contributes. The catalogue lists
each parallel matrix job of the same build separately, so the same bug appears
up to five times, and importing all of them would inflate the dataset with
copies of one failure.
"""

import argparse
import json
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

API_URL = "http://www.api.bugswarm.org/v1/artifacts"
PAGE_SIZE = 100
REQUEST_TIMEOUT_SECONDS = 60
DEFAULT_LIMIT = 12
DEFAULT_PER_REPO = 2
# A page count high enough to reach the limit, low enough not to sweep the
# whole catalogue when the filter matches little.
MAX_PAGES = 8

QUALITY_FILTER: dict[str, Any] = {
    "lang": "Python",
    "reproducibility_status.status": "Reproducible",
    "stability": "5/5",
    "classification.code": "Yes",
    "classification.test": "No",
    "classification.build": "No",
    "metrics.num_of_changed_files": 1,
}


def fetch_page(page: int) -> list[dict[str, Any]]:
    query = urllib.parse.quote(json.dumps(QUALITY_FILTER))
    url = f"{API_URL}?where={query}&max_results={PAGE_SIZE}&page={page}"
    with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.load(response)
    items: list[dict[str, Any]] = payload.get("_items", [])
    return items


def candidates(limit: int, per_repo: int) -> Iterator[dict[str, Any]]:
    """Artifacts passing the filter, deduplicated and capped per repository."""
    seen_bugs: set[tuple[str, str]] = set()
    per_repo_count: dict[str, int] = {}
    taken = 0

    for page in range(1, MAX_PAGES + 1):
        items = fetch_page(page)
        if not items:
            return
        for artifact in items:
            failed_tests = artifact["failed_job"].get("failed_tests") or ""
            repo = artifact["repo"]
            if not failed_tests:
                continue
            # The same bug appears once per parallel job in the build matrix.
            bug = (repo, failed_tests)
            if bug in seen_bugs:
                continue
            if per_repo_count.get(repo, 0) >= per_repo:
                continue
            seen_bugs.add(bug)
            per_repo_count[repo] = per_repo_count.get(repo, 0) + 1
            taken += 1
            yield artifact
            if taken >= limit:
                return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--per-repo", type=int, default=DEFAULT_PER_REPO)
    parser.add_argument("--json", action="store_true", help="Emit full records, not just tags.")
    args = parser.parse_args()

    chosen = list(candidates(args.limit, args.per_repo))
    if args.json:
        print(json.dumps(chosen, indent=2))
        return 0

    for artifact in chosen:
        tests = artifact["failed_job"]["failed_tests"].replace("\n", " ")[:60]
        print(f"# {artifact['repo']:35s} {artifact['test_framework']:9s} {tests}")
    print(",".join(artifact["image_tag"] for artifact in chosen))
    print(f"\n{len(chosen)} artifacts selected", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
