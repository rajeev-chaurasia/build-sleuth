"""Regression tests for issues found in QA review of the condense module."""

import pytest

from buildsleuth.condense.clean import clean_log, normalize_line_breaks
from buildsleuth.condense.patterns import ALL_GROUPS
from buildsleuth.condense.router import MIN_MAX_CHARS, condense
from buildsleuth.models.context import CondenseStrategy


def _matching_groups(line: str) -> set[str]:
    return {
        group.name for group in ALL_GROUPS if any(regex.search(line) for regex in group.regexes)
    }


def test_lone_carriage_return_becomes_a_line_break() -> None:
    """Progress-bar rewrites use lone CR; leaving them in desynchronizes line numbers."""
    assert normalize_line_breaks("a\rb\r\nc\n") == "a\nb\nc\n"


def test_condense_line_numbers_agree_with_newline_split() -> None:
    raw = "start\r10%\r20%\rdone\n##[error]boom\n"
    cleaned = clean_log(raw)
    lines = cleaned.rstrip("\n").split("\n")

    result = condense(cleaned, max_chars=MIN_MAX_CHARS)
    excerpt = result.excerpts[0]

    assert result.total_lines == len(lines)
    assert excerpt.text == "\n".join(lines[excerpt.start_line - 1 : excerpt.end_line])


def test_timestamps_after_a_lone_carriage_return_are_stripped() -> None:
    raw = "2026-08-05T10:00:00.0000000Z first\r2026-08-05T10:00:01.0000000Z ##[error]late"
    assert clean_log(raw) == "first\n##[error]late"


def test_budget_below_minimum_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_chars"):
        condense("some log text", max_chars=10)


def test_oversized_single_line_is_marked_truncated() -> None:
    result = condense("x" * 5_000, max_chars=MIN_MAX_CHARS)
    excerpt = result.excerpts[0]

    assert result.strategy == CondenseStrategy.TAIL
    assert excerpt.text.startswith("...[truncated]")
    assert len(result.as_text()) <= MIN_MAX_CHARS


def test_empty_log_produces_no_excerpts() -> None:
    result = condense("", max_chars=MIN_MAX_CHARS)
    assert result.total_lines == 0
    assert result.excerpts == []


@pytest.mark.parametrize(
    "line",
    [
        "KilledTaskHandler cleaned up",
        "task_killed_count=0",
        "TLS handshake completed in 12ms",
    ],
)
def test_innocent_lines_do_not_match_failure_patterns(line: str) -> None:
    assert _matching_groups(line) == set()


@pytest.mark.parametrize(
    ("line", "group"),
    [
        ("Killed", "oom_disk"),
        ("bash: line 1:  1234 Killed    pytest -q", "oom_disk"),
        ("TLS handshake timeout after 30s", "timeout_network"),
    ],
)
def test_genuine_failure_lines_still_match(line: str, group: str) -> None:
    assert group in _matching_groups(line)
