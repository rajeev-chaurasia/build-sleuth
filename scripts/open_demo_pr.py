"""Open the draft pull request for a verified patch.

Run as `uv run python scripts/open_demo_pr.py --result <demo_result.json> --repo owner/name`.

Separate from the demo script on purpose: everything up to here is read only,
and this is the one step that writes.
"""

import argparse
import json
from pathlib import Path

from buildsleuth.config import load_settings
from buildsleuth.guardrails.allowlist import check_write_target
from buildsleuth.guardrails.patch_policy import check_patch
from buildsleuth.models.taxonomy import FailureClass
from buildsleuth.pipeline.verify import VerificationLevel, check_applies, normalize_patch
from buildsleuth.providers.github.client import GitHubClient
from buildsleuth.providers.github.writer import GitHubWriter, branch_name, build_pr_body

DEFAULT_BASE = "main"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--run-url", default="")
    parser.add_argument("--model", default="gemini-3.1-flash-lite")
    parser.add_argument("--file", required=True, help="Path the patch edits.")
    parser.add_argument("--patched", type=Path, required=True, help="File with the fixed content.")
    parser.add_argument(
        "--repo-dir",
        type=Path,
        default=Path.cwd(),
        help="Checkout the patch was written against, which is the failing tree.",
    )
    args = parser.parse_args()

    settings = load_settings()
    check_write_target(args.repo, settings)

    result = json.loads(args.result.read_text(encoding="utf-8"))
    verdict = result["verdict"]
    fix = result["fix"]
    patch = normalize_patch(fix["patch"])

    failure_class = FailureClass(verdict["failure_class"])
    violation = check_patch(patch, failure_class)
    if violation is not None:
        raise SystemExit(f"patch policy refused this: {violation.reason}")

    applied = check_applies(patch, args.repo_dir)
    if applied.level < VerificationLevel.APPLIES:
        raise SystemExit(f"patch does not apply: {applied.detail}")

    token = settings.github_token.get_secret_value() if settings.github_token else None
    if not token:
        raise SystemExit("opening a pull request needs BUILDSLEUTH_GITHUB_TOKEN")

    client = GitHubClient(token=token)
    try:
        writer = GitHubWriter(client)
        base_sha = str(client.get_ref(args.repo, f"heads/{args.base}")["object"]["sha"])
        branch = branch_name(int(result.get("run_id", 0)) or 1)

        writer.create_branch_commit(
            repo=args.repo,
            base_sha=base_sha,
            branch=branch,
            files={args.file: args.patched.read_text(encoding="utf-8")},
            message=f"fix: {fix['strategy'][:70]}",
        )
        body = build_pr_body(
            failure_class=verdict["failure_class"],
            subcategory=verdict["subcategory"],
            strategy=fix["strategy"],
            expected_effect=fix["expected_effect"],
            evidence=verdict.get("evidence", []),
            verification=(
                f"- Patch applies cleanly to `{args.base}` (`git apply --check`)\n"
                f"- Patch policy passed: within size limits, no credential, no workflow edit\n"
                f"- Not executed in CI by this tool; the checks on this pull request are the test"
            ),
            run_url=args.run_url,
            model=args.model,
        )
        pull = writer.open_draft_pr(
            repo=args.repo,
            branch=branch,
            base=args.base,
            title=f"fix: {fix['strategy'][:60]}",
            body=body,
        )
    finally:
        client.close()

    print(f"draft pull request opened: {pull.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
