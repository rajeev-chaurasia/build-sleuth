"""Hybrid condensation router.

Greps error patterns and emits context windows when they fit the character
budget, otherwise falls back to the log tail. This routing dominates on the
LogDx-CI benchmark versus either strategy alone.
"""

from dataclasses import dataclass, field

from buildsleuth.condense.patterns import ALL_GROUPS
from buildsleuth.models.context import CondensedLog, CondenseStrategy, LogExcerpt

CONTEXT_LINES_BEFORE = 20
CONTEXT_LINES_AFTER = 20
TAIL_LINES = 200
# Roughly a 6k-token budget at 4 chars per token; chars keep this dependency-free.
DEFAULT_MAX_CHARS = 24_000

TAIL_REASON = "tail"
# Smallest budget that can still hold an excerpt header plus some content.
MIN_MAX_CHARS = 200
_REASON_SEPARATOR = ","
# Mirrors the "\n\n" joiner in CondensedLog.as_text so budget math stays exact.
_EXCERPT_SEPARATOR_LENGTH = len("\n\n")
_TRUNCATION_MARKER = "...[truncated]"

_GROUP_PRIORITY: dict[str, int] = {group.name: rank for rank, group in enumerate(ALL_GROUPS)}


@dataclass
class _Window:
    """Inclusive 1-based line span plus the pattern groups that produced it."""

    start: int
    end: int
    groups: set[str] = field(default_factory=set)


def condense(cleaned_log: str, max_chars: int = DEFAULT_MAX_CHARS) -> CondensedLog:
    """Condense a cleaned log to error context windows, or the tail as fallback.

    Expects text from clean_log, whose only line separator is "\\n".
    """
    if max_chars < MIN_MAX_CHARS:
        raise ValueError(f"max_chars must be at least {MIN_MAX_CHARS}, got {max_chars}")
    lines = _split_lines(cleaned_log)
    total_lines = len(lines)
    windows = _merge_windows(_find_matches(lines), total_lines)
    excerpts = [_window_excerpt(window, lines) for window in windows]
    excerpts = _longest_fitting_suffix(excerpts, total_lines, max_chars)
    if excerpts:
        return CondensedLog(
            excerpts=excerpts,
            strategy=CondenseStrategy.ERROR_WINDOWS,
            total_lines=total_lines,
        )
    return _tail(lines, total_lines, max_chars)


def _split_lines(cleaned_log: str) -> list[str]:
    """Split on "\\n" only, so line numbers match the cleaned text exactly.

    str.splitlines would also break on lone CR and other Unicode separators,
    which would leave line numbers disagreeing with the text we report.
    """
    if not cleaned_log:
        return []
    lines = cleaned_log.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def _find_matches(lines: list[str]) -> list[tuple[int, str]]:
    """Return (1-based line number, group name) pairs in scan order."""
    matches: list[tuple[int, str]] = []
    for line_no, line in enumerate(lines, start=1):
        for group in ALL_GROUPS:
            if any(regex.search(line) for regex in group.regexes):
                matches.append((line_no, group.name))
    return matches


def _merge_windows(matches: list[tuple[int, str]], total_lines: int) -> list[_Window]:
    """Expand matches to context windows and merge overlapping or adjacent ones."""
    windows: list[_Window] = []
    for line_no, group_name in matches:
        start = max(1, line_no - CONTEXT_LINES_BEFORE)
        end = min(total_lines, line_no + CONTEXT_LINES_AFTER)
        if windows and start <= windows[-1].end + 1:
            windows[-1].end = max(windows[-1].end, end)
            windows[-1].groups.add(group_name)
        else:
            windows.append(_Window(start=start, end=end, groups={group_name}))
    return windows


def _window_excerpt(window: _Window, lines: list[str]) -> LogExcerpt:
    names = sorted(window.groups, key=lambda name: _GROUP_PRIORITY[name])
    return LogExcerpt(
        start_line=window.start,
        end_line=window.end,
        text="\n".join(lines[window.start - 1 : window.end]),
        reason=_REASON_SEPARATOR.join(names),
    )


def _longest_fitting_suffix(
    excerpts: list[LogExcerpt], total_lines: int, max_chars: int
) -> list[LogExcerpt]:
    """Drop earliest excerpts until the rendering fits the budget.

    Later windows are usually closer to the real failure, so the kept set is
    always a suffix. Scanning from the end keeps this linear in log size.
    """
    kept = 0
    rendered = 0
    for excerpt in reversed(excerpts):
        part = _rendered_length([excerpt], total_lines)
        candidate = rendered + part + (_EXCERPT_SEPARATOR_LENGTH if kept else 0)
        if candidate > max_chars:
            break
        rendered = candidate
        kept += 1
    return excerpts[len(excerpts) - kept :]


def _rendered_length(excerpts: list[LogExcerpt], total_lines: int) -> int:
    """Size of the final as_text() output, which is what the budget applies to."""
    candidate = CondensedLog(
        excerpts=excerpts,
        strategy=CondenseStrategy.ERROR_WINDOWS,
        total_lines=total_lines,
    )
    return len(candidate.as_text())


def _tail(lines: list[str], total_lines: int, max_chars: int) -> CondensedLog:
    """Fallback: the last TAIL_LINES lines, truncated from the front to fit."""

    def too_big(candidate: list[str]) -> bool:
        return _rendered_length([_tail_excerpt(candidate, total_lines)], total_lines) > max_chars

    tail = lines[-TAIL_LINES:]
    while len(tail) > 1 and too_big(tail):
        tail.pop(0)
    if not tail:
        return CondensedLog(excerpts=[], strategy=CondenseStrategy.TAIL, total_lines=total_lines)
    excerpt = _tail_excerpt(tail, total_lines)
    overflow = _rendered_length([excerpt], total_lines) - max_chars
    if overflow > 0:
        # A lone oversized line: keep its end, where the failure detail usually is.
        kept = excerpt.text[overflow + len(_TRUNCATION_MARKER) :]
        excerpt = excerpt.model_copy(update={"text": _TRUNCATION_MARKER + kept})
    return CondensedLog(
        excerpts=[excerpt],
        strategy=CondenseStrategy.TAIL,
        total_lines=total_lines,
    )


def _tail_excerpt(tail: list[str], total_lines: int) -> LogExcerpt:
    return LogExcerpt(
        start_line=total_lines - len(tail) + 1,
        end_line=total_lines,
        text="\n".join(tail),
        reason=TAIL_REASON,
    )
