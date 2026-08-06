"""Turn two blind labellers' verdicts into dataset cases.

Run as `uv run python scripts/reconcile_labels.py --labels mined/labels.json`.

Only cases where both labellers independently agree on class, subcategory and
diff relatedness become dataset entries. Everything else is written to a
review file, because a case two experts read differently is either genuinely
ambiguous or a hole in the taxonomy, and both are worth a person's attention.
"""

import argparse
import json
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from buildsleuth.models.case import (
    CaseInputs,
    CaseSource,
    GroundTruth,
    LabelingMethod,
    Provenance,
    TriageCase,
    Verification,
)
from buildsleuth.models.taxonomy import FailureClass, is_valid_subcategory

ADJUDICATORS = 2
LOG_FILE = "failed_job.txt"
DIFF_FILE = "diff.patch"
CASE_LOG_PATH = "logs/failed_job.txt"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Verdict:
    """One labeller's answer for one case."""

    failure_class: str
    subcategory: str
    related_to_diff: str
    culprit_files: list[str] = field(default_factory=list)
    confidence: float = 0.0
    evidence: str = ""
    reasoning: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        """What has to match for two labellers to count as agreeing."""
        return (self.failure_class, self.subcategory, self.related_to_diff)


def _related_to_diff(raw: str) -> bool | None:
    if raw == UNKNOWN:
        return None
    return raw == "true"


def _shared_culprits(first: Verdict, second: Verdict) -> list[str]:
    """Keep paths both labellers named, in the first labeller's order.

    A path only one of them saw is a guess by definition, and a wrong path
    costs more than a missing one because somebody goes and reads it.
    """
    other = {path.strip() for path in second.culprit_files}
    return [path for path in first.culprit_files if path.strip() in other]


def build_case(
    case_id: str, slug: str, meta: dict[str, Any], agreed: Verdict, other: Verdict
) -> TriageCase:
    failure_class = FailureClass(agreed.failure_class)
    if not is_valid_subcategory(failure_class, agreed.subcategory):
        raise ValueError(f"{slug}: {agreed.subcategory} is not valid for {failure_class}")

    confidence = min(agreed.confidence, other.confidence)
    return TriageCase(
        case_id=case_id,
        title=f"{meta['repo']} {meta['failed_job']}"[:120],
        inputs=CaseInputs(
            repo=str(meta["repo"]),
            run_id=int(meta["run_id"]),
            run_attempt=int(meta.get("run_attempt") or 1),
            head_sha=str(meta["head_sha"]),
            pr_number=meta.get("pr_number"),
            failed_job_name=str(meta["failed_job"]),
            failed_step_name=meta.get("failed_step"),
            log_files=[CASE_LOG_PATH],
            diff_file=DIFF_FILE if meta.get("has_diff") else None,
        ),
        ground_truth=GroundTruth(
            failure_class=failure_class,
            subcategory=agreed.subcategory,
            related_to_diff=_related_to_diff(agreed.related_to_diff),
            culprit_files=_shared_culprits(agreed, other),
        ),
        provenance=Provenance(
            source=CaseSource.GHA_MINED,
            labeling_method=LabelingMethod.LOG_EVIDENCE,
            verified_by_human=False,
            original_url=meta.get("original_url"),
            snapshot_date=date.today(),
            independent_adjudications=ADJUDICATORS,
            adjudicators_agreeing=ADJUDICATORS,
            notes=(
                f"Two blind labellers independently agreed on {failure_class.value}/"
                f"{agreed.subcategory}. Lowest confidence of the two was {confidence:.2f}."
                f" Decisive evidence: {agreed.evidence[:240]}"
            ),
        ),
        verification=Verification(),
        tags=[],
    )


def reconcile(
    labels: dict[str, dict[str, Any]], mined: Path, dataset: Path, start: int
) -> tuple[int, list[str]]:
    """Write agreed cases into the dataset and return what needs review."""
    agreed_count = 0
    review: list[str] = []
    next_id = start

    for slug in sorted(labels):
        entry = labels[slug]
        first = Verdict(**entry["a"])
        second = Verdict(**entry["b"])
        source = mined / slug

        if first.key != second.key:
            review.append(f"{slug}: A said {'/'.join(first.key)}, B said {'/'.join(second.key)}")
            continue
        if not (source / LOG_FILE).exists():
            review.append(f"{slug}: evidence missing from {source}")
            continue

        meta = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
        case_id = f"gm-{next_id:04d}"
        target = dataset / "cases" / "gha-mined" / case_id
        (target / "logs").mkdir(parents=True, exist_ok=True)

        shutil.copy(source / LOG_FILE, target / CASE_LOG_PATH)
        if meta.get("has_diff") and (source / DIFF_FILE).exists():
            shutil.copy(source / DIFF_FILE, target / DIFF_FILE)

        case = build_case(case_id, slug, meta, first, second)
        (target / "case.json").write_text(
            case.model_dump_json(indent=2, exclude_none=False) + "\n", encoding="utf-8"
        )
        agreed_count += 1
        next_id += 1

    return agreed_count, review


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=Path("mined/labels.json"))
    parser.add_argument("--mined", type=Path, default=Path("mined"))
    parser.add_argument("--dataset", type=Path, default=Path("dataset"))
    parser.add_argument("--start-id", type=int, default=100)
    args = parser.parse_args()

    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    agreed, review = reconcile(labels, args.mined, args.dataset, args.start_id)

    print(f"{agreed} case(s) written where both labellers agreed")
    print(f"{len(review)} case(s) need a person to look:")
    for note in review:
        print(f"  {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
