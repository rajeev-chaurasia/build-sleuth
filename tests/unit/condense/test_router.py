"""Tests for the hybrid condensation router."""

from pathlib import Path

from buildsleuth.condense.clean import clean_log
from buildsleuth.condense.router import TAIL_LINES, condense
from buildsleuth.models.context import CondenseStrategy

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "logs"


def _load(name: str) -> str:
    return clean_log((FIXTURES / name).read_text(encoding="utf-8"))


def _routine_lines(count: int) -> list[str]:
    return [f"line {i} of routine output" for i in range(1, count + 1)]


def test_nearby_matches_merge_into_one_excerpt() -> None:
    lines = _routine_lines(100)
    lines[39] = "FAILED tests/test_a.py::test_a - boom"  # line 40
    lines[49] = "npm ERR! code ERESOLVE"  # line 50
    result = condense("\n".join(lines))
    assert result.strategy is CondenseStrategy.ERROR_WINDOWS
    assert len(result.excerpts) == 1
    excerpt = result.excerpts[0]
    assert excerpt.start_line == 20
    assert excerpt.end_line == 70
    assert excerpt.reason == "pytest,dependency"
    assert result.total_lines == 100


def test_distant_matches_produce_separate_excerpts() -> None:
    lines = _routine_lines(200)
    lines[9] = "MemoryError"  # line 10
    lines[149] = "Killed"  # line 150
    result = condense("\n".join(lines))
    assert [(e.start_line, e.end_line) for e in result.excerpts] == [(1, 30), (130, 170)]
    assert all(e.reason == "oom_disk" for e in result.excerpts)


def test_budget_overflow_drops_earliest_windows_first() -> None:
    lines = _routine_lines(600)
    lines[49] = "MemoryError near the start"  # line 50
    lines[499] = "Killed near the end"  # line 500
    text = "\n".join(lines)
    full = condense(text)
    assert len(full.excerpts) == 2
    trimmed = condense(text, max_chars=len(full.as_text()) - 1)
    assert trimmed.strategy is CondenseStrategy.ERROR_WINDOWS
    assert len(trimmed.excerpts) == 1
    assert trimmed.excerpts[0].start_line == 480
    assert trimmed.excerpts[0].end_line == 520
    assert "Killed near the end" in trimmed.excerpts[0].text


def test_no_match_log_falls_back_to_tail() -> None:
    cleaned = _load("no_match_chatter.txt")
    lines = cleaned.splitlines()
    total = len(lines)
    assert total > TAIL_LINES
    result = condense(cleaned)
    assert result.strategy is CondenseStrategy.TAIL
    assert result.total_lines == total
    assert len(result.excerpts) == 1
    excerpt = result.excerpts[0]
    assert excerpt.start_line == total - TAIL_LINES + 1
    assert excerpt.end_line == total
    assert excerpt.reason == "tail"
    assert excerpt.text.splitlines() == lines[-TAIL_LINES:]


def test_oversized_single_window_degrades_to_tail() -> None:
    lines = _routine_lines(300)
    lines[249] = "MemoryError"  # line 250, its window alone exceeds the budget
    budget = 500
    result = condense("\n".join(lines), max_chars=budget)
    assert result.strategy is CondenseStrategy.TAIL
    assert len(result.as_text()) <= budget
    excerpt = result.excerpts[0]
    assert excerpt.reason == "tail"
    assert excerpt.end_line == 300
    assert excerpt.start_line > 300 - TAIL_LINES


def test_pytest_fixture_produces_one_window_at_known_lines() -> None:
    cleaned = _load("pytest_failure.txt")
    lines = cleaned.splitlines()
    # Known fixture positions: first match (FAILURES banner) at 72, last at 92.
    assert "FAILURES" in lines[71]
    assert lines[91] == "##[error]Process completed with exit code 1."
    result = condense(cleaned)
    assert result.strategy is CondenseStrategy.ERROR_WINDOWS
    assert result.total_lines == 92
    assert len(result.excerpts) == 1
    excerpt = result.excerpts[0]
    assert excerpt.start_line == 52
    assert excerpt.end_line == 92
    assert excerpt.reason == "pytest,python_traceback,actions_runner"
    assert excerpt.text.splitlines()[0] == lines[51]
    assert excerpt.text.splitlines()[-1] == lines[91]
    assert "FAILED tests/test_rate_limits.py::test_burst_refill" in excerpt.text


def test_npm_fixture_window_and_reason() -> None:
    result = condense(_load("npm_dependency.txt"))
    assert result.strategy is CondenseStrategy.ERROR_WINDOWS
    assert result.total_lines == 70
    assert len(result.excerpts) == 1
    excerpt = result.excerpts[0]
    assert (excerpt.start_line, excerpt.end_line) == (30, 70)
    assert excerpt.reason == "dependency,actions_runner"


def test_compiler_fixture_window_and_reason() -> None:
    result = condense(_load("compiler_error.txt"))
    assert result.total_lines == 69
    assert len(result.excerpts) == 1
    excerpt = result.excerpts[0]
    assert (excerpt.start_line, excerpt.end_line) == (28, 69)
    assert excerpt.reason == "compiler,actions_runner"
    assert "error[E0308]: mismatched types" in excerpt.text


def test_oom_fixture_window_and_reason() -> None:
    result = condense(_load("oom.txt"))
    assert result.total_lines == 60
    assert len(result.excerpts) == 1
    excerpt = result.excerpts[0]
    assert (excerpt.start_line, excerpt.end_line) == (22, 60)
    assert excerpt.reason == "actions_runner,oom_disk"


def test_as_text_contains_reason_tags() -> None:
    windows_text = condense(_load("compiler_error.txt")).as_text()
    assert "[lines 28-69, compiler,actions_runner]" in windows_text
    tail_text = condense(_load("no_match_chatter.txt")).as_text()
    assert "[lines 84-283, tail]" in tail_text
