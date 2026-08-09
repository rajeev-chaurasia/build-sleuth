"""Check the verifier by running patches whose outcome is already known.

Run as `uv run python -m evals.verifier_control`.

A fix funnel that reports nothing but failures has two explanations: the model
writes bad patches, or the verifier cannot recognise a good one. Those need
opposite responses, and the funnel alone cannot tell them apart.

So each case is verified twice, with no model involved:

- the maintainer's own fix, which must reach the top rung
- that same fix with one line corrupted, which must not

A case where the real fix fails is a broken case or a broken verifier, and it
is excluded from the funnel rather than counted against the model. A case
where the corrupted fix passes means the check is not checking anything.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from buildsleuth.dataset.loader import case_dir_for, load_cases
from buildsleuth.models.case import TriageCase
from buildsleuth.pipeline.verify import VerificationLevel
from buildsleuth.sandbox.bugswarm_runner import verify_in_image
from evals.fix_funnel import executable_cases, image_tag

FIX_DIFF_FILE = "fix.diff"
RESULTS_DIR = Path("results")
DEFAULT_DATASET = Path("dataset")
# Enough to break the patch without making it unparseable, so it fails at
# apply time the way a genuinely wrong patch does.
# A line that cannot occur in any of these sources, so the hunk it belongs
# to can never match.
CORRUPTION = "buildsleuth_control_line_that_does_not_exist"
CONTEXT_MARKER = " "
REMOVED_MARKER = "-"
FILE_MARKERS = ("--- ", "-- ")
OUTPUT_TAIL_CHARS = 400


def corrupt(diff: str) -> str:
    """Damage the patch so it cannot apply, in a way nothing will repair.

    Deliberately not the hunk header. That was the first version, and
    `patch_repair` recomputes headers from the body, so it quietly undid the
    corruption: the broken patch applied, the build passed, and nine good
    cases were reported as unable to measure anything.

    Damaging a context line instead cannot be recovered from, because the
    line it has to match no longer exists in the file.
    """
    lines = diff.splitlines()
    for index, line in enumerate(lines):
        if line.startswith((CONTEXT_MARKER, REMOVED_MARKER)) and not line.startswith(FILE_MARKERS):
            lines[index] = line[0] + CORRUPTION
            break
    return "\n".join(lines) + "\n"


def reference_diff(dataset: Path, case: TriageCase) -> str | None:
    path = case_dir_for(dataset, case) / FIX_DIFF_FILE
    return path.read_text(encoding="utf-8") if path.is_file() else None


def check_case(dataset: Path, case: TriageCase) -> dict[str, object]:
    """Verify the real fix and a corrupted one, and say whether both behaved."""
    diff = reference_diff(dataset, case)
    if not diff:
        return {"case_id": case.case_id, "usable": False, "reason": "no reference fix stored"}

    tag = image_tag(case)
    real = verify_in_image(diff, tag, case.inputs.repo)
    broken = verify_in_image(corrupt(diff), tag, case.inputs.repo)

    real_passes = real.level is VerificationLevel.NOTHING_ELSE_BROKE
    broken_fails = broken.level < VerificationLevel.NOTHING_ELSE_BROKE

    if not real_passes:
        reason = f"reference fix only reached {real.level.name}"
    elif not broken_fails:
        # The build passes whatever is applied, so nothing about a patch can
        # be concluded from it. Previously recorded with no reason at all.
        reason = "the corrupted fix passed too, so the build proves nothing"
    else:
        reason = ""

    return {
        "case_id": case.case_id,
        "usable": real_passes and broken_fails,
        "reference_level": real.level.name,
        "corrupted_level": broken.level.name,
        "reason": reason,
        "detail": real.detail,
        # Kept because a case marked unusable is a case somebody has to
        # diagnose, and the level alone does not say what went wrong.
        "output_tail": real.stdout_tail[-OUTPUT_TAIL_CHARS:],
    }


def summarize(directory: Path) -> int:
    """Combine per-case control files written on separate runners."""
    cases: list[dict[str, object]] = []
    for path in sorted(directory.rglob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if "cases" in record and "n_usable" in record:
            cases.extend(record["cases"])

    if not cases:
        print(f"no control files under {directory}")
        return 0

    usable = [case for case in cases if case["usable"]]
    for case in cases:
        if not case["usable"]:
            print(f"  {case['case_id']}: unusable, {case.get('reason')}")
    print(f"\n{len(usable)} of {len(cases)} cases can measure a fix")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--only", default="", help="Comma separated case ids to check.")
    parser.add_argument(
        "--summarize", type=Path, default=None, help="Combine control files from per-case runs."
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if args.summarize is not None:
        return summarize(args.summarize)

    cases = executable_cases(load_cases(args.dataset))
    wanted = {name.strip() for name in args.only.split(",") if name.strip()}
    if wanted:
        cases = [case for case in cases if case.case_id in wanted]

    results = []
    for case in cases:
        result = check_case(args.dataset, case)
        results.append(result)
        verdict = "usable" if result["usable"] else f"UNUSABLE: {result.get('reason')}"
        print(f"  {case.case_id:10s} {case.inputs.repo:28s} {verdict}", flush=True)

    usable = [r for r in results if r["usable"]]
    print(f"\n{len(usable)} of {len(results)} cases can measure a fix")

    out = args.out or RESULTS_DIR / "verifier-control.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "n_usable": len(usable),
                "n_checked": len(results),
                "cases": results,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
