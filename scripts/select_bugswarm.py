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

The same reasoning applies across runs, not just within one. Selection reads
the cases already committed and carries their tags, bugs and per-repository
counts in as a starting point, so a second run proposes artifacts the dataset
does not already have rather than the same ones under a sibling job id.
"""

import argparse
import json
import urllib.parse
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

API_URL = "http://www.api.bugswarm.org/v1/artifacts"
PAGE_SIZE = 100
REQUEST_TIMEOUT_SECONDS = 60
DEFAULT_LIMIT = 12
DEFAULT_PER_REPO = 2
# A page count high enough to reach the limit, low enough not to sweep the
# whole catalogue when the filter matches little.
MAX_PAGES = 8

DEFAULT_CASES_DIR = Path(__file__).resolve().parent.parent / "dataset" / "cases" / "bugswarm"
CASE_FILE_NAME = "case.json"
IMAGE_TAG_SEPARATOR = ":"
FAILED_TEST_SEPARATOR = "#"

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


class Existing(NamedTuple):
    """What the committed dataset already holds, in the shapes selection dedupes on."""

    tags: set[str]
    bugs: set[tuple[str, tuple[str, ...]]]
    repo_counts: dict[str, int]


def bug_key(repo: str, failed_tests: str) -> tuple[str, tuple[str, ...]]:
    """Identify a bug by its repository and the set of tests it failed."""
    names = tuple(sorted(name for name in failed_tests.split(FAILED_TEST_SEPARATOR) if name))
    return (repo, names)


def read_existing(cases_dir: Path) -> Existing:
    """Tags, bugs and per-repository counts of the cases already imported."""
    existing = Existing(tags=set(), bugs=set(), repo_counts={})
    for case_file in sorted(cases_dir.glob(f"*/{CASE_FILE_NAME}")):
        case: dict[str, Any] = json.loads(case_file.read_text(encoding="utf-8"))
        image = case.get("verification", {}).get("docker_image", "")
        if IMAGE_TAG_SEPARATOR in image:
            existing.tags.add(image.rsplit(IMAGE_TAG_SEPARATOR, 1)[1])
        repo = case.get("inputs", {}).get("repo", "")
        if not repo:
            continue
        tests = case.get("ground_truth", {}).get("failing_tests") or []
        existing.bugs.add(bug_key(repo, FAILED_TEST_SEPARATOR.join(tests)))
        existing.repo_counts[repo] = existing.repo_counts.get(repo, 0) + 1
    return existing


def candidates(
    limit: int, per_repo: int, existing: Existing | None = None
) -> Iterator[dict[str, Any]]:
    """Artifacts passing the filter, deduplicated and capped per repository."""
    existing = existing or Existing(set(), set(), {})
    seen_bugs = set(existing.bugs)
    per_repo_count = dict(existing.repo_counts)
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
            if artifact["image_tag"] in existing.tags:
                continue
            # The same bug appears once per parallel job in the build matrix.
            bug = bug_key(repo, failed_tests)
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
    parser.add_argument(
        "--cases-dir",
        type=Path,
        default=DEFAULT_CASES_DIR,
        help="Cases already imported, excluded from the selection.",
    )
    args = parser.parse_args()

    existing = read_existing(args.cases_dir)
    chosen = list(candidates(args.limit, args.per_repo, existing))
    if args.json:
        print(json.dumps(chosen, indent=2))
        return 0

    print(f"# skipping {len(existing.tags)} artifacts already in the dataset")
    for artifact in chosen:
        tests = artifact["failed_job"]["failed_tests"].replace("\n", " ")[:60]
        print(f"# {artifact['repo']:35s} {artifact['test_framework']:9s} {tests}")
    print(",".join(artifact["image_tag"] for artifact in chosen))
    print(f"\n{len(chosen)} artifacts selected", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
