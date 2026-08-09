"""Score proposed patches by how far they got, not by whether they looked good.

Reported as a funnel. A single pass rate hides the difference between a model
that writes patches which do not apply and one that writes patches which apply
but do not fix anything, and those need opposite responses.

Patches are never scored by asking a model whether they seem right. Execution
decides, and where execution stopped is the number.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from buildsleuth.pipeline.verify import VerificationLevel

DECIMALS = 3


@dataclass(frozen=True)
class FixAttempt:
    """One case's outcome, including the cases where no patch was tried."""

    case_id: str
    attempted: bool
    level: VerificationLevel = VerificationLevel.NOTHING
    skip_reason: str = ""
    # Why it stopped where it did. The rung is the metric; this is what makes
    # a number that moved between runs diagnosable rather than mysterious.
    detail: str = ""
    evidence: str = ""
    # Whether the model was even editing the right file. A patch for the
    # wrong file cannot fix anything, and separating the two says which
    # stage to work on.
    localized: bool = False
    # Whether the model's own diff was refused and only the copy with
    # recomputed hunk headers applied. Counted so the repair has to earn its
    # place rather than being assumed to help.
    needed_repair: bool = False


class FixReport(BaseModel):
    """How far patches got, as counts and as rates over what was attempted."""

    n_cases: int
    n_attempted: int
    n_skipped: int
    applies: int
    lints: int
    failing_test_passes: int
    nothing_else_broke: int

    @property
    def apply_rate(self) -> float:
        return self.applies / self.n_attempted if self.n_attempted else 0.0

    @property
    def fix_rate(self) -> float:
        """Share of attempted patches that actually made the failure stop."""
        return self.failing_test_passes / self.n_attempted if self.n_attempted else 0.0

    @property
    def clean_fix_rate(self) -> float:
        """Fixed the failure and broke nothing else, which is the only real success."""
        return self.nothing_else_broke / self.n_attempted if self.n_attempted else 0.0


def fix_metrics(attempts: Sequence[FixAttempt]) -> FixReport:
    """Count how far each attempted patch got.

    Skipped cases are counted separately rather than as failures. Declining to
    patch a flake is correct behaviour, and scoring it as a miss would push a
    model toward guessing.
    """
    attempted = [attempt for attempt in attempts if attempt.attempted]

    def at_least(level: VerificationLevel) -> int:
        return sum(1 for attempt in attempted if attempt.level >= level)

    return FixReport(
        n_cases=len(attempts),
        n_attempted=len(attempted),
        n_skipped=len(attempts) - len(attempted),
        applies=at_least(VerificationLevel.APPLIES),
        lints=at_least(VerificationLevel.LINTS),
        failing_test_passes=at_least(VerificationLevel.FAILING_TEST_PASSES),
        nothing_else_broke=at_least(VerificationLevel.NOTHING_ELSE_BROKE),
    )


def render_funnel(report: FixReport) -> str:
    """Markdown funnel showing where patches stopped."""
    if not report.n_attempted:
        return f"No patch was attempted on any of the {report.n_cases} cases."

    rows = [
        ("patch attempted", report.n_attempted),
        ("applies cleanly", report.applies),
        ("passes lint", report.lints),
        ("failing test passes", report.failing_test_passes),
        ("nothing else broke", report.nothing_else_broke),
    ]
    lines = [
        "| stage | cases | share of attempted |",
        "| --- | --- | --- |",
    ]
    for label, count in rows:
        share = count / report.n_attempted
        lines.append(f"| {label} | {count} | {share:.{DECIMALS}f} |")
    lines.append(f"| not attempted, by design | {report.n_skipped} | |")
    return "\n".join(lines)
