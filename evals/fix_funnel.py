"""Measure how far proposed patches actually get, on cases that can be run.

Run as `uv run python -m evals.fix_funnel --model gemini-3.1-flash-lite`.

Classification and localization are scored on every case in the benchmark,
because scoring them needs only a log and a label. Fix quality is different:
deciding whether a patch works means running it, and most cases store a log
and a diff rather than a runnable repository.

So this runs only over cases carrying a BugSwarm image, where the repository
sits at the failing commit beside the script the original job ran. Everything
else is reported as skipped rather than quietly excluded, because a funnel
computed over an unstated subset is the kind of number this project exists to
distrust.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from buildsleuth.config import load_settings
from buildsleuth.dataset.loader import case_dir_for, load_cases, read_case_log
from buildsleuth.llm.client import OpenAICompatClient
from buildsleuth.llm.registry import Provider, get_model_spec
from buildsleuth.models.case import TriageCase, VerificationMethod
from buildsleuth.pipeline.fix import SkipReason, propose_fix
from buildsleuth.pipeline.localize import localize, ranked_paths
from buildsleuth.pipeline.triage import TriageContext, classify
from buildsleuth.pipeline.verify import VerificationLevel
from buildsleuth.sandbox.bugswarm_runner import read_files_in_image, verify_in_image
from buildsleuth.tools.evidence import Evidence
from evals.scorers.fix_quality import FixAttempt, fix_metrics, render_funnel

DEFAULT_MODEL = "gemini-3.1-flash-lite"
DEFAULT_DATASET = Path("dataset")
RESULTS_DIR = Path("results")
IMAGE_TAG_PREFIX = "bugswarm/cached-images:"
NO_IMAGE = "case has no runnable image"
NO_PATCH = "model declined to patch"
# Enough for a rejected-hunk report without bloating the committed record.
EVIDENCE_CHARS = 600
UNKNOWN_SHA = "unknown"
# How the runner words an apply that only succeeded after the hunk headers
# were recomputed.
REPAIR_NOTE = "repairing its hunk headers"
CONTROL_FILE = RESULTS_DIR / "verifier-control.json"


def unmeasurable_cases(control: Path = CONTROL_FILE) -> set[str]:
    """Cases where the maintainer's own fix does not pass the verifier.

    Those cannot distinguish a bad patch from a case the harness cannot run,
    so they are dropped from the funnel entirely rather than scored as
    failures. Counting them would blame the model for a broken case.
    """
    if not control.is_file():
        return set()
    record = json.loads(control.read_text(encoding="utf-8"))
    return {case["case_id"] for case in record.get("cases", []) if not case.get("usable")}


def executable_cases(cases: list[TriageCase]) -> list[TriageCase]:
    """Cases whose patches can be judged by running something."""
    return [
        case
        for case in cases
        if case.verification is not None
        and case.verification.method == VerificationMethod.BUGSWARM_IMAGE
        and case.verification.docker_image
    ]


def image_tag(case: TriageCase) -> str:
    image = case.verification.docker_image if case.verification else None
    return (image or "").removeprefix(IMAGE_TAG_PREFIX)


def read_files_for(case: TriageCase, paths: list[str]) -> dict[str, str]:
    """The files the model wants to edit, taken from the artifact itself.

    Not from GitHub at the same commit. BugSwarm patches a repository so its
    build reproduces, so the upstream file can differ from the one the patch
    will be applied to. Fetching the upstream copy made the model write
    patches whose context matched nothing, and two cases that had been fixed
    cleanly stopped applying at all.

    Without this the model is handed no source and asked to write a unified
    diff anyway, which measures whether it can guess the surrounding lines.
    """
    return read_files_in_image(image_tag(case), case.inputs.repo, paths)


def attempt_one(client: OpenAICompatClient, model: str, case: TriageCase, log: str) -> FixAttempt:
    """Run the pipeline for one case and record how far the patch got."""
    evidence = Evidence(log_text=log)
    triaged = classify(
        client, model, TriageContext.from_log(log, repo=case.inputs.repo, failed_job="build")
    )
    verdict = triaged.value

    located = localize(client, model, verdict, evidence, repo=case.inputs.repo)
    paths = ranked_paths(located.value) if located is not None else []

    # Whether the patch could ever have worked. A fix rate alone cannot say
    # if the model wrote a bad patch or patched the wrong file, and at this
    # sample size that difference is most of the movement between runs.
    on_target = bool(set(paths) & set(case.ground_truth.culprit_files))

    contents = read_files_for(case, paths)
    print(
        f"    localized {paths} ({'on target' if on_target else 'wrong file'}),"
        f" read {len(contents)} file(s)",
        flush=True,
    )
    # Keep the localizer's paths as the editable set. Narrowing it to the
    # files that happened to be readable leaves the model with nothing it is
    # allowed to touch when the read comes back empty.
    proposed = propose_fix(client, model, verdict, evidence, paths, contents, repo=case.inputs.repo)
    if isinstance(proposed, SkipReason):
        return FixAttempt(
            case_id=case.case_id,
            attempted=False,
            skip_reason=proposed.reason,
            localized=on_target,
        )

    # The case records owner/name, which resolves the checkout exactly.
    result = verify_in_image(proposed.value.patch, image_tag(case), case.inputs.repo)
    return FixAttempt(
        case_id=case.case_id,
        attempted=True,
        level=result.level,
        # The rung alone says a patch failed, not why. Without the reason a
        # run that moves cannot be told apart from a harness that changed.
        detail=result.detail,
        evidence=result.stdout_tail[-EVIDENCE_CHARS:],
        localized=on_target,
        needed_repair=REPAIR_NOTE in result.detail,
    )


def read_attempts(directory: Path) -> list[FixAttempt]:
    """Rebuild attempts from the files per-case runs wrote."""
    attempts: list[FixAttempt] = []
    for path in sorted(directory.rglob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        for entry in record.get("attempts", []):
            level = entry.get("level")
            attempts.append(
                FixAttempt(
                    case_id=entry["case_id"],
                    attempted=entry["attempted"],
                    level=VerificationLevel[level] if level else VerificationLevel.NOTHING,
                    skip_reason=entry.get("skip_reason", ""),
                    detail=entry.get("detail", ""),
                    evidence=entry.get("evidence", ""),
                    localized=entry.get("localized", False),
                    needed_repair=entry.get("needed_repair", False),
                )
            )
    return attempts


def aggregate(directory: Path, out: Path) -> int:
    """Combine per-case runs into one funnel.

    Each case runs on its own machine because the images are gigabytes and
    two of them will not fit on one disk, so the funnel has to be assembled
    afterwards rather than accumulated in a single process.
    """
    attempts = read_attempts(directory)
    if not attempts:
        print(f"no attempt files under {directory}")
        return 1

    report = fix_metrics(attempts)
    print(render_funnel(report))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "report": report.model_dump(mode="json"),
                "attempts": [
                    {
                        "case_id": a.case_id,
                        "attempted": a.attempted,
                        "level": a.level.name if a.attempted else None,
                        "skip_reason": a.skip_reason,
                        "detail": a.detail,
                        "evidence": a.evidence,
                        "localized": a.localized,
                        "needed_repair": a.needed_repair,
                    }
                    for a in sorted(attempts, key=lambda a: a.case_id)
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--limit", type=int, default=0, help="Stop after this many cases.")
    parser.add_argument("--only", default="", help="Comma separated case ids to run.")
    parser.add_argument(
        "--aggregate",
        type=Path,
        default=None,
        help="Combine attempt files written by per-case runs into one funnel.",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if args.aggregate is not None:
        return aggregate(args.aggregate, args.out or RESULTS_DIR / "fix-funnel.json")

    settings = load_settings()
    spec = get_model_spec(args.model)
    api_key = settings.api_key_for(spec.provider)
    if spec.provider is not Provider.OLLAMA and not api_key:
        print(f"no key configured for {spec.provider}")
        return 1
    client = OpenAICompatClient(spec=spec, api_key=api_key)

    every_case = load_cases(args.dataset)
    runnable = executable_cases(every_case)
    wanted = {name.strip() for name in args.only.split(",") if name.strip()}
    if wanted:
        runnable = [case for case in runnable if case.case_id in wanted]
    unmeasurable = unmeasurable_cases()
    excluded = [case.case_id for case in runnable if case.case_id in unmeasurable]
    runnable = [case for case in runnable if case.case_id not in unmeasurable]
    if args.limit:
        runnable = runnable[: args.limit]

    print(f"{len(runnable)} of {len(every_case)} cases can measure a fix", flush=True)
    if excluded:
        # Said out loud, because a funnel over a silently narrowed subset is
        # the kind of number this harness exists to distrust.
        print(f"excluded by the verifier control: {', '.join(excluded)}", flush=True)
    print(flush=True)

    attempts: list[FixAttempt] = []
    for case in runnable:
        log = read_case_log(case_dir_for(args.dataset, case), case)
        try:
            attempt = attempt_one(client, args.model, case, log)
        except Exception as error:
            # A case that blew up is a case with no result, not a pass.
            attempt = FixAttempt(
                case_id=case.case_id, attempted=False, skip_reason=f"{type(error).__name__}"
            )
        attempts.append(attempt)
        reached = attempt.level.name if attempt.attempted else f"skipped: {attempt.skip_reason}"
        print(f"  {case.case_id:10s} {case.inputs.repo:30s} {reached}", flush=True)

    report = fix_metrics(attempts)
    print("\n" + render_funnel(report))

    out = args.out or RESULTS_DIR / f"fix-funnel-{args.model}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "model": args.model,
                "generated_at": datetime.now(UTC).isoformat(),
                "n_cases_in_dataset": len(every_case),
                "report": report.model_dump(mode="json"),
                "attempts": [
                    {
                        "case_id": a.case_id,
                        "attempted": a.attempted,
                        "level": a.level.name if a.attempted else None,
                        "skip_reason": a.skip_reason,
                        "detail": a.detail,
                        "evidence": a.evidence,
                        "localized": a.localized,
                        "needed_repair": a.needed_repair,
                    }
                    for a in attempts
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {out}")
    # A model that declines to patch is a recorded outcome, not a failed
    # run. Returning non-zero there failed two jobs that had worked.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
