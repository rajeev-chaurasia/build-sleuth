"""Stage: rank the files that would have to change to fix the failure.

Runs after classification, because the class narrows the search: an
infrastructure failure or a flake has no culprit file at all, and answering
with one anyway is worse than answering with none.
"""

from buildsleuth.condense.router import DEFAULT_MAX_CHARS, condense
from buildsleuth.llm.structured import StructuredResult, complete_structured
from buildsleuth.llm.types import Message, ModelClient, Role
from buildsleuth.models.taxonomy import FailureClass
from buildsleuth.models.triage import MAX_RANKED_FILES, Localization, TriageVerdict
from buildsleuth.prompts.loader import Prompt, load_prompt
from buildsleuth.tools.evidence import Evidence

LOCALIZE_PROMPT_NAME = "localize"
NO_DIFF_FILES = "none captured for this run"
UNKNOWN_FIELD = "unknown"

# A failure the repository source did not cause has nothing to localize, so
# the model is not asked. Guessing here produces a path someone then reads.
_SOURCELESS_CLASSES = frozenset({FailureClass.FLAKY_TEST, FailureClass.INFRA_ENVIRONMENT})


def has_locatable_culprit(verdict: TriageVerdict) -> bool:
    return verdict.failure_class not in _SOURCELESS_CLASSES


def build_messages(
    verdict: TriageVerdict,
    evidence: Evidence,
    prompt: Prompt,
    repo: str = UNKNOWN_FIELD,
    failed_job: str = UNKNOWN_FIELD,
    failed_step: str = UNKNOWN_FIELD,
    max_log_chars: int = DEFAULT_MAX_CHARS,
) -> list[Message]:
    diff_files = evidence.diff_paths()
    body = prompt.render(
        failure_class=verdict.failure_class.value,
        subcategory=verdict.subcategory,
        reasoning=verdict.reasoning or "not stated",
        repo=repo,
        failed_job=failed_job,
        failed_step=failed_step,
        condensed_log=condense(evidence.log_text, max_chars=max_log_chars).as_text(),
        diff_files="\n".join(diff_files) if diff_files else NO_DIFF_FILES,
    )
    return [Message(role=Role.USER, content=body)]


def localize(
    client: ModelClient,
    model: str,
    verdict: TriageVerdict,
    evidence: Evidence,
    prompt: Prompt | None = None,
    repo: str = UNKNOWN_FIELD,
    failed_job: str = UNKNOWN_FIELD,
    failed_step: str = UNKNOWN_FIELD,
) -> StructuredResult[Localization] | None:
    """Rank likely culprit files, or None when the class implies there are none."""
    if not has_locatable_culprit(verdict):
        return None

    active = prompt if prompt is not None else load_prompt(LOCALIZE_PROMPT_NAME)
    result = complete_structured(
        client=client,
        model=model,
        messages=build_messages(verdict, evidence, active, repo, failed_job, failed_step),
        output_model=Localization,
    )
    result.value.ranked_files = result.value.ranked_files[:MAX_RANKED_FILES]
    return result


def ranked_paths(localization: Localization | None) -> list[str]:
    """Just the paths, in order, for the scorer."""
    if localization is None:
        return []
    return [candidate.path for candidate in localization.ranked_files]
