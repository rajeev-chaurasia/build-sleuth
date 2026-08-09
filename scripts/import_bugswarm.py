"""Import BugSwarm artifacts as cases whose patches can actually be executed.

Run as `uv run python scripts/import_bugswarm.py --tags a,b,c`.

These are the only cases in the benchmark where fix quality can be scored,
because the image carries the repository at the failing commit and a script
that reruns the build. Their ground truth is also stronger than the mined
cases: the culprit files come from the maintainer's own fix.

Images are large, so importing is slow and deliberate rather than a sweep.
"""

import argparse
import json
from datetime import date
from pathlib import Path

from buildsleuth.dataset.bugswarm import (
    IMAGE_PREFIX,
    ArtifactError,
    ArtifactMetadata,
    build_artifact,
    discard_image,
    extract_script,
    fetch_metadata,
    in_image,
    parse_tag,
    repo_name_from_slug,
    verification_commands,
)
from buildsleuth.models.case import (
    CaseInputs,
    CaseSource,
    GroundTruth,
    LabelingMethod,
    Provenance,
    TriageCase,
    Verification,
    VerificationMethod,
)
from buildsleuth.models.taxonomy import FailureClass

CASE_LOG_PATH = "logs/failed_job.txt"
FIX_DIFF_FILE = "fix.diff"
DEFAULT_SUBCATEGORY = "test_assertion"
UNKNOWN_SHA = "unknown"


def build_case(
    case_id: str, tag: str, artifact_files: list[str], metadata: ArtifactMetadata | None
) -> TriageCase:
    slug, job_id = parse_tag(tag)
    catalogue_note = (
        " The commit and the failing test names come from the BugSwarm"
        " catalogue entry, which rates this artifact reproducible at 5/5."
        if metadata is not None
        else " The catalogue was unreachable, so the commit is unknown and no"
        " failing tests are recorded."
    )
    return TriageCase(
        case_id=case_id,
        title=f"{slug} build {job_id}"[:120],
        inputs=CaseInputs(
            repo=metadata.repo if metadata else slug.replace("-", "/", 1),
            run_id=job_id,
            head_sha=metadata.head_sha if metadata else UNKNOWN_SHA,
            failed_job_name="build",
            log_files=[CASE_LOG_PATH],
        ),
        ground_truth=GroundTruth(
            failure_class=FailureClass.CODE_CHANGE,
            subcategory=DEFAULT_SUBCATEGORY,
            # The artifact pairs a failing commit with the fix, so the failure
            # follows from the code at that commit by construction.
            related_to_diff=True,
            culprit_files=artifact_files,
            failing_tests=metadata.failing_tests if metadata else [],
        ),
        provenance=Provenance(
            source=CaseSource.BUGSWARM,
            labeling_method=LabelingMethod.IMPORTED,
            verified_by_human=False,
            snapshot_date=date.today(),
            notes=(
                "Imported from BugSwarm artifact "
                f"{tag}. Culprit files are the ones the maintainer's own fix"
                " touched, taken by diffing the failing and passing trees in"
                " the image, rather than read off the log by a labeller. The"
                " class follows from the artifact being a reproducible build"
                " failure that a human then fixed; the subcategory is a"
                " default and has not been adjudicated." + catalogue_note
            ),
        ),
        verification=Verification(
            method=VerificationMethod.BUGSWARM_IMAGE,
            docker_image=f"{IMAGE_PREFIX}:{tag}",
            test_command=verification_commands(tag)[0],
        ),
        tags=["bugswarm", "executable"],
    )


def import_one(tag: str, dataset: Path, case_id: str, keep_image: bool = False) -> str:
    slug, job_id = parse_tag(tag)
    metadata = fetch_metadata(tag)
    # The catalogue names the repository exactly; the slug only implies it.
    checkout = metadata.repo_name if metadata else repo_name_from_slug(slug)
    try:
        output = in_image(tag, extract_script(job_id, checkout))
        artifact = build_artifact(tag, output)
    finally:
        # Always, including on failure. A partially imported artifact still
        # left gigabytes behind, and a batch of those fills the disk.
        if not keep_image:
            discard_image(tag)

    target = dataset / "cases" / "bugswarm" / case_id
    (target / "logs").mkdir(parents=True, exist_ok=True)
    (target / CASE_LOG_PATH).write_text(artifact.failing_log, encoding="utf-8", newline="\n")
    if artifact.fix_diff:
        (target / FIX_DIFF_FILE).write_text(artifact.fix_diff, encoding="utf-8", newline="\n")

    case = build_case(case_id, tag, artifact.culprit_files, metadata)
    (target / "case.json").write_text(
        case.model_dump_json(indent=2, exclude_none=False) + "\n", encoding="utf-8"
    )
    lines = artifact.failing_log.count("\n")
    return f"{case_id}: {tag}, {lines} log lines, {len(artifact.culprit_files)} culprit file(s)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tags", required=True, help="Comma separated BugSwarm image tags.")
    parser.add_argument("--dataset", type=Path, default=Path("dataset"))
    parser.add_argument("--start-id", type=int, default=1)
    parser.add_argument(
        "--keep-images", action="store_true", help="Do not delete images after extraction."
    )
    args = parser.parse_args()

    tags = [tag.strip() for tag in args.tags.split(",") if tag.strip()]
    imported = 0
    for index, tag in enumerate(tags):
        case_id = f"bs-{args.start_id + index:04d}"
        try:
            print(import_one(tag, args.dataset, case_id, args.keep_images), flush=True)
            imported += 1
        except (ArtifactError, OSError, json.JSONDecodeError) as error:
            print(f"  {tag}: skipped, {type(error).__name__}: {error}", flush=True)

    print(f"\n{imported} of {len(tags)} artifacts imported")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
