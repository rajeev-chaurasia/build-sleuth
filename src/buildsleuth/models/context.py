"""Models produced by log condensation."""

from enum import StrEnum

from pydantic import BaseModel


class CondenseStrategy(StrEnum):
    ERROR_WINDOWS = "error_windows"
    TAIL = "tail"


class LogExcerpt(BaseModel):
    """A contiguous slice of the cleaned log.

    Line numbers are 1-based and reference the cleaned full log, so an agent
    can ask for surrounding lines and get a coherent answer.
    """

    start_line: int
    end_line: int
    text: str
    reason: str  # which pattern group matched, or "tail"


class CondensedLog(BaseModel):
    excerpts: list[LogExcerpt]
    strategy: CondenseStrategy
    total_lines: int  # line count of the cleaned full log

    def as_text(self) -> str:
        return "\n\n".join(
            f"[lines {e.start_line}-{e.end_line}, {e.reason}]\n{e.text}" for e in self.excerpts
        )
