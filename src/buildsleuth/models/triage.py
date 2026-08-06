"""Models an agent produces. Every one of these is a proposal, never a decision."""

from pydantic import BaseModel, Field

from buildsleuth.models.taxonomy import FailureClass

MAX_RANKED_FILES = 5


class TriageVerdict(BaseModel):
    failure_class: FailureClass
    subcategory: str
    related_to_diff: bool
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = []  # quoted log lines supporting the verdict
    reasoning: str = ""


class FileCandidate(BaseModel):
    path: str
    confidence: float = Field(ge=0.0, le=1.0)
    why: str = ""


class Localization(BaseModel):
    """Culprit files, best first.

    The cap is a presentation limit, not a correctness one, so an overlong
    answer is trimmed by the localize stage rather than rejected. Spending a
    repair call to argue about a sixth entry is not worth the tokens.
    """

    ranked_files: list[FileCandidate] = Field(default_factory=list)
