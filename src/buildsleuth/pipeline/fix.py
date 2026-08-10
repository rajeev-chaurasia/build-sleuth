"""Stage: propose a patch for a localized failure.

Deliberately narrow. A patch is only attempted when the class implies the
repository source is at fault and the classifier was confident enough to be
worth acting on. For a flake or a runner outage there is nothing to patch,
and a confident-looking diff for one of those wastes a reviewer's time in the
most annoying way possible.
"""

from dataclasses import dataclass

from pydantic import BaseModel

from buildsleuth.condense.router import DEFAULT_MAX_CHARS, condense
from buildsleuth.llm.structured import StructuredResult, complete_structured
from buildsleuth.llm.types import Message, ModelClient, Role
from buildsleuth.models.taxonomy import FailureClass
from buildsleuth.models.triage import EditProposal, FixProposal, TriageVerdict
from buildsleuth.prompts.loader import Prompt, load_prompt
from buildsleuth.tools.evidence import Evidence

FIX_PROMPT_NAME = "fix"
EDITS_PROMPT_NAME = "fix_edits"
MIN_CONFIDENCE = 0.6
MAX_FILE_CHARS = 12_000
NO_FILES = "none supplied"
NO_DIFF = "not captured"
UNKNOWN_FIELD = "unknown"

# Only these classes have a repository file that could be edited to help.
_PATCHABLE_CLASSES = frozenset({FailureClass.CODE_CHANGE, FailureClass.PIPELINE_CONFIG})


@dataclass(frozen=True)
class SkipReason:
    """Why no patch was attempted, in words a person can act on."""

    reason: str


def should_attempt(verdict: TriageVerdict, culprit_files: list[str]) -> SkipReason | None:
    """Return None when a patch is worth attempting, else why it is not."""
    if verdict.failure_class not in _PATCHABLE_CLASSES:
        return SkipReason(
            f"{verdict.failure_class.value} failures have no repository file to patch."
            " Rerun, pin, or quarantine instead."
        )
    if verdict.confidence < MIN_CONFIDENCE:
        return SkipReason(
            f"classifier confidence {verdict.confidence:.2f} is below {MIN_CONFIDENCE:.2f},"
            " so a patch would be built on a guess about the cause"
        )
    if not culprit_files:
        return SkipReason("no culprit file was identified, so there is nothing to edit")
    return None


def render_file_contents(contents: dict[str, str]) -> str:
    """Lay out the files the model may edit, truncating any that are huge."""
    if not contents:
        return NO_FILES

    blocks: list[str] = []
    for path, body in contents.items():
        text = body if len(body) <= MAX_FILE_CHARS else body[:MAX_FILE_CHARS] + "\n...[truncated]"
        blocks.append(f"--- {path} ---\n{text}")
    return "\n\n".join(blocks)


def build_messages(
    verdict: TriageVerdict,
    evidence: Evidence,
    culprit_files: list[str],
    file_contents: dict[str, str],
    prompt: Prompt,
    repo: str = UNKNOWN_FIELD,
    max_log_chars: int = DEFAULT_MAX_CHARS,
) -> list[Message]:
    body = prompt.render(
        repo=repo,
        failure_class=verdict.failure_class.value,
        subcategory=verdict.subcategory,
        reasoning=verdict.reasoning or "not stated",
        condensed_log=condense(evidence.log_text, max_chars=max_log_chars).as_text(),
        culprit_files="\n".join(culprit_files) if culprit_files else NO_FILES,
        file_contents=render_file_contents(file_contents),
        diff=evidence.diff_text or NO_DIFF,
    )
    return [Message(role=Role.USER, content=body)]


def _propose[T: BaseModel](
    client: ModelClient,
    model: str,
    verdict: TriageVerdict,
    evidence: Evidence,
    culprit_files: list[str],
    file_contents: dict[str, str],
    prompt: Prompt | None,
    prompt_name: str,
    output_model: type[T],
    repo: str,
) -> StructuredResult[T] | SkipReason:
    skip = should_attempt(verdict, culprit_files)
    if skip is not None:
        return skip

    active = prompt if prompt is not None else load_prompt(prompt_name)
    return complete_structured(
        client=client,
        model=model,
        messages=build_messages(verdict, evidence, culprit_files, file_contents, active, repo=repo),
        output_model=output_model,
    )


def propose_fix(
    client: ModelClient,
    model: str,
    verdict: TriageVerdict,
    evidence: Evidence,
    culprit_files: list[str],
    file_contents: dict[str, str],
    prompt: Prompt | None = None,
    repo: str = UNKNOWN_FIELD,
) -> StructuredResult[FixProposal] | SkipReason:
    """Ask for a unified diff, or explain why a patch was not worth attempting."""
    return _propose(
        client,
        model,
        verdict,
        evidence,
        culprit_files,
        file_contents,
        prompt,
        FIX_PROMPT_NAME,
        FixProposal,
        repo,
    )


def propose_edits(
    client: ModelClient,
    model: str,
    verdict: TriageVerdict,
    evidence: Evidence,
    culprit_files: list[str],
    file_contents: dict[str, str],
    prompt: Prompt | None = None,
    repo: str = UNKNOWN_FIELD,
) -> StructuredResult[EditProposal] | SkipReason:
    """Ask for anchored edits, which is the preferred way to get a patch.

    The model names the text to change instead of writing a diff, and
    `pipeline.anchored_edits` computes the diff from the file. Context lines
    then come from the checkout rather than from the model's memory of it,
    which is what the unified diff path keeps getting wrong.
    """
    return _propose(
        client,
        model,
        verdict,
        evidence,
        culprit_files,
        file_contents,
        prompt,
        EDITS_PROMPT_NAME,
        EditProposal,
        repo,
    )
