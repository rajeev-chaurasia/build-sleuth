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


class FixProposal(BaseModel):
    """A candidate patch, which nothing has verified yet.

    An empty patch is a legitimate answer, not a failure. A model that cannot
    fix the cause should say so rather than produce something that makes the
    red turn green by removing the check.
    """

    patch: str = ""
    strategy: str = ""
    expected_effect: str = ""
    touched_files: list[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.patch.strip()
