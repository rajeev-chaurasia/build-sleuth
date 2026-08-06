"""One off: record the maintainer's sign-off on every label.

Run after a person has read the evidence and the adjudication disagreements
and made the calls. Kept as a script so the decision lands as a reviewable
diff rather than a hand edit nobody can audit.
"""

import json
from pathlib import Path

DATASET = Path("dataset/cases")
VERIFIED_DATE = "2026-08-06"

DECISIONS = {
    "gm-0001": "Maintainer confirmed code_change/lint_or_format and ruled that"
    " related_to_diff stays null: with no diff captured, the agent is not shown"
    " one either, so the question is not fair to score.",
    "gm-0002": "Maintainer confirmed code_change/dependency_conflict and the"
    " correction of related_to_diff to null.",
    "gm-0003": "Maintainer confirmed pipeline_config and chose"
    " build_step_misconfig over the two existing values the labellers reached"
    " for, on the grounds that the automatic builder cannot build this project.",
    "gm-0004": "Maintainer confirmed code_change/policy_gate and ruled"
    " related_to_diff stays null for the same reason as gm-0001.",
    "gm-0005": "Maintainer confirmed code_change/compile_error with"
    " related_to_diff true, the one case where log and diff corroborate.",
    "sy-0001": "Maintainer confirmed flaky_test/async_wait for the constructed"
    " case, noting concurrency remains a defensible alternative subcategory.",
}


def apply() -> int:
    changed = 0
    for path in sorted(DATASET.glob("*/*/case.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        note = DECISIONS.get(case["case_id"])
        if note is None:
            continue

        provenance = case["provenance"]
        provenance["verified_by_human"] = True
        provenance["verified_date"] = VERIFIED_DATE
        provenance["notes"] = f"{provenance['notes']} {note}".strip()

        tags = case.setdefault("tags", [])
        if "smoke" not in tags:
            tags.append("smoke")

        path.write_text(json.dumps(case, indent=2) + "\n", encoding="utf-8")
        changed += 1
    return changed


if __name__ == "__main__":
    print(f"recorded sign-off on {apply()} cases")
