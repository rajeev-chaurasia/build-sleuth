"""One off: apply the outcome of the blind adjudication round to the dataset.

Two labellers each saw only the evidence, neither saw the recorded label nor
each other. Their verdicts are folded in here so the change is reviewable as
a diff rather than hidden in an interactive session.
"""

import json
from pathlib import Path
from typing import Any

DATASET = Path("dataset/cases")
ADJUDICATORS = 2

# case_id -> (agreeing count, field updates, note appended to provenance)
DECISIONS: dict[str, tuple[int, dict[str, Any], str]] = {
    "gm-0001": (
        1,
        {"related_to_diff": None},
        "Blind adjudication: both labellers agreed on code_change/lint_or_format."
        " They split on related_to_diff, one reading the log's own PR reference as"
        " sufficient and one holding that no diff means no verification. Set to null"
        " because the case carries no diff, so the question is not answerable from"
        " the evidence a model is given.",
    ),
    "gm-0002": (
        2,
        {"related_to_diff": None},
        "Blind adjudication: both labellers agreed on code_change/dependency_conflict"
        " and both judged related_to_diff unverifiable without a diff, against the"
        " previous value of true which rested on the branch name. Corrected to null."
        " One labeller noted the log cannot distinguish a version that was never"
        " published from one yanked after the pin merged.",
    ),
    "gm-0003": (
        1,
        {"subcategory": "build_step_misconfig"},
        "Blind adjudication: both labellers agreed on pipeline_config and both"
        " reported that no existing subcategory described the failure, one choosing"
        " action_version and one matrix_or_trigger while calling it a poor fit."
        " build_step_misconfig was added to the taxonomy for this and the label"
        " moved to it. Both noted the meson stderr is not in the captured log, so"
        " the shallow checkout explanation is inference.",
    ),
    "gm-0004": (
        1,
        {"subcategory": "policy_gate", "related_to_diff": None},
        "Blind adjudication: both labellers agreed on code_change and both said"
        " lint_or_format was a forced fit for a contribution policy gate, so"
        " policy_gate was added to the taxonomy and the label moved to it. They"
        " split on related_to_diff; set to null since the case carries no diff.",
    ),
    "gm-0005": (
        2,
        {},
        "Blind adjudication: both labellers independently reached"
        " code_change/compile_error with related_to_diff true, citing the same"
        " ImportError and the diff that introduces it. One noted memory.py carries"
        " the same import and would fail too.",
    ),
    "sy-0001": (
        2,
        {},
        "Blind adjudication: both labellers independently reached"
        " flaky_test/async_wait, citing the passing rerun of the same commit."
        " Both flagged that concurrency is a defensible alternative subcategory,"
        " since an item was still in flight when the wait expired.",
    ),
}


def apply() -> int:
    changed = 0
    for case_dir in sorted(DATASET.glob("*/*/")):
        path = case_dir / "case.json"
        case = json.loads(path.read_text(encoding="utf-8"))
        decision = DECISIONS.get(case["case_id"])
        if decision is None:
            continue

        agreeing, updates, note = decision
        case["ground_truth"].update(updates)
        provenance = case["provenance"]
        provenance["independent_adjudications"] = ADJUDICATORS
        provenance["adjudicators_agreeing"] = agreeing
        provenance["notes"] = f"{provenance['notes']} {note}".strip()

        if agreeing == ADJUDICATORS:
            case.setdefault("tags", [])
            if "smoke" not in case["tags"]:
                case["tags"].append("smoke")

        path.write_text(json.dumps(case, indent=2) + "\n", encoding="utf-8")
        changed += 1
    return changed


if __name__ == "__main__":
    print(f"updated {apply()} cases")
