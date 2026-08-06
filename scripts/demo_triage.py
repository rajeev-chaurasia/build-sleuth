"""Run one failure through every stage and print what each one decided.

Run as `uv run python scripts/demo_triage.py <log-file> [--repo owner/name]`.

This is the end to end demo. It stops before writing anything: opening the
pull request is a separate, guarded step.
"""

import argparse
import json
from pathlib import Path

from buildsleuth.config import load_settings
from buildsleuth.guardrails.patch_policy import check_patch
from buildsleuth.llm.client import OpenAICompatClient
from buildsleuth.llm.registry import Provider, get_model_spec
from buildsleuth.pipeline.fix import SkipReason, propose_fix
from buildsleuth.pipeline.localize import localize, ranked_paths
from buildsleuth.pipeline.triage import TriageContext, classify
from buildsleuth.pipeline.verify import check_applies
from buildsleuth.tools.evidence import Evidence

DEFAULT_MODEL = "gemini-3.1-flash-lite"
PASSED = "passed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--repo", default="unknown")
    parser.add_argument("--job", default="unknown")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--files", default="", help="Comma separated paths the model may edit.")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    settings = load_settings()
    spec = get_model_spec(args.model)
    client = OpenAICompatClient(spec=spec, api_key=settings.api_key_for(Provider.GEMINI))

    log_text = args.log.read_text(encoding="utf-8", errors="replace")
    evidence = Evidence(log_text=log_text)

    triaged = classify(
        client,
        args.model,
        TriageContext.from_log(log_text, repo=args.repo, failed_job=args.job),
    )
    verdict = triaged.value
    print(
        f"1 classify   {verdict.failure_class}/{verdict.subcategory} (confidence {verdict.confidence})"
    )
    print(f"             {verdict.reasoning[:150]}")

    located = localize(client, args.model, verdict, evidence, repo=args.repo)
    paths = ranked_paths(located.value) if located is not None else []
    print(f"2 localize   {paths or 'no culprit file, by design for this class'}")

    editable = [path.strip() for path in args.files.split(",") if path.strip()] or paths
    contents = {
        path: Path(path).read_text(encoding="utf-8") for path in editable if Path(path).is_file()
    }

    proposed = propose_fix(
        client, args.model, verdict, evidence, list(contents), contents, repo=args.repo
    )
    if isinstance(proposed, SkipReason):
        print(f"3 fix        declined: {proposed.reason}")
        return 0

    fix = proposed.value
    print(f"3 fix        {fix.strategy[:140]}")
    print(f"             touches {fix.touched_files}")

    violation = check_patch(fix.patch, verdict.failure_class)
    print(f"4 policy     {violation.reason if violation else PASSED}")

    applied = check_applies(fix.patch, Path.cwd())
    print(f"5 verify     {applied.level.name}: {applied.detail}")

    if args.out is not None:
        args.out.write_text(
            json.dumps(
                {
                    "verdict": verdict.model_dump(mode="json"),
                    "ranked_files": paths,
                    "fix": fix.model_dump(mode="json"),
                    "policy": violation.reason if violation else PASSED,
                    "verification": applied.level.name,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
