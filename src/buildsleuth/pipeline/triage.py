"""Stage: classify a failure from its condensed log and diff.

The only LLM call in the classification path. Everything it returns is
validated against the taxonomy before it leaves this module, so a model that
invents a subcategory produces a recorded miss rather than a bad metric.
"""

from dataclasses import dataclass

from buildsleuth.condense.router import DEFAULT_MAX_CHARS, condense
from buildsleuth.llm.structured import StructuredResult, complete_structured
from buildsleuth.llm.types import Message, ModelClient, Role
from buildsleuth.models.taxonomy import SUBCATEGORIES, is_valid_subcategory
from buildsleuth.models.triage import TriageVerdict
from buildsleuth.prompts.loader import Prompt, load_prompt

TRIAGE_PROMPT_NAME = "triage"
NO_DIFF_PLACEHOLDER = "no diff available"
UNKNOWN_FIELD = "unknown"
MAX_DIFF_CHARS = 8_000


@dataclass(frozen=True)
class TriageContext:
    """Everything the prompt needs, already reduced to text."""

    repo: str
    workflow_name: str
    failed_job: str
    failed_step: str
    run_attempt: int
    condensed_log: str
    diff: str

    @classmethod
    def from_log(
        cls,
        log_text: str,
        diff_text: str | None = None,
        repo: str = UNKNOWN_FIELD,
        workflow_name: str = UNKNOWN_FIELD,
        failed_job: str = UNKNOWN_FIELD,
        failed_step: str = UNKNOWN_FIELD,
        run_attempt: int = 1,
        max_log_chars: int = DEFAULT_MAX_CHARS,
    ) -> "TriageContext":
        """Condense a full log and truncate the diff to fit the context budget."""
        return cls(
            repo=repo,
            workflow_name=workflow_name,
            failed_job=failed_job,
            failed_step=failed_step,
            run_attempt=run_attempt,
            condensed_log=condense(log_text, max_chars=max_log_chars).as_text(),
            diff=_truncate_diff(diff_text),
        )


def _truncate_diff(diff_text: str | None) -> str:
    if not diff_text:
        return NO_DIFF_PLACEHOLDER
    if len(diff_text) <= MAX_DIFF_CHARS:
        return diff_text
    return diff_text[:MAX_DIFF_CHARS] + "\n...[diff truncated]"


class InvalidVerdictError(Exception):
    """The model answered with a subcategory that is not in the taxonomy."""


def check_verdict(verdict: TriageVerdict) -> TriageVerdict:
    """Reject a verdict whose subcategory does not belong to its class."""
    if not is_valid_subcategory(verdict.failure_class, verdict.subcategory):
        allowed = ", ".join(sorted(SUBCATEGORIES[verdict.failure_class]))
        raise InvalidVerdictError(
            f"{verdict.subcategory!r} is not a subcategory of {verdict.failure_class}."
            f" Expected one of: {allowed}"
        )
    return verdict


def build_messages(context: TriageContext, prompt: Prompt) -> list[Message]:
    body = prompt.render(
        repo=context.repo,
        workflow_name=context.workflow_name,
        failed_job=context.failed_job,
        failed_step=context.failed_step,
        run_attempt=str(context.run_attempt),
        condensed_log=context.condensed_log,
        diff=context.diff,
    )
    return [Message(role=Role.USER, content=body)]


def classify(
    client: ModelClient,
    model: str,
    context: TriageContext,
    prompt: Prompt | None = None,
) -> StructuredResult[TriageVerdict]:
    """Ask the model to classify the failure and validate what comes back."""
    active_prompt = prompt if prompt is not None else load_prompt(TRIAGE_PROMPT_NAME)
    result = complete_structured(
        client=client,
        model=model,
        messages=build_messages(context, active_prompt),
        output_model=TriageVerdict,
    )
    check_verdict(result.value)
    return result
