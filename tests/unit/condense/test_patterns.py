"""Table-driven checks for the error pattern registry."""

import pytest

from buildsleuth.condense.patterns import ALL_GROUPS, PatternGroup

_BY_NAME: dict[str, PatternGroup] = {group.name: group for group in ALL_GROUPS}

MATCHING: list[tuple[str, str]] = [
    ("pytest", "FAILED tests/test_rate_limits.py::test_burst_refill - AssertionError"),
    ("pytest", "ERROR tests/test_db.py - sqlalchemy.exc.OperationalError"),
    ("pytest", "E       AssertionError: assert 5 == 4"),
    ("pytest", "=================================== FAILURES ==================================="),
    ("pytest", "==================================== ERRORS ===================================="),
    ("pytest", "tests/test_rate_limits.py:18: AssertionError"),
    ("python_traceback", "Traceback (most recent call last):"),
    ("compiler", "error[E0308]: mismatched types"),
    ("compiler", "error: could not compile `telemetry-agent` due to 1 previous error"),
    ("compiler", "src/main.c:42:5: error: expected declaration specifiers"),
    ("compiler", "SyntaxError: unexpected token ')'"),
    ("compiler", "TypeError: cannot read properties of undefined (reading 'map')"),
    ("dependency", "ERROR: Could not find a version that satisfies the requirement torch==9.9"),
    ("dependency", "pip._internal.exceptions.ResolutionImpossible: for help visit the docs"),
    ("dependency", "npm ERR! code ERESOLVE"),
    ("dependency", "GET https://registry.npmjs.org/left-pad - 404 Not Found"),
    ("dependency", "FetchError: request failed, reason: connect ETIMEDOUT 104.16.0.1:443"),
    ("actions_runner", "##[error]Process completed with exit code 1."),
    ("actions_runner", "Process completed with exit code 137."),
    ("oom_disk", "java.lang.OutOfMemoryError: Java heap space"),
    ("oom_disk", "Killed"),
    ("oom_disk", "OSError: [Errno 28] No space left on device"),
    ("oom_disk", "MemoryError"),
    ("timeout_network", "Error: The operation was canceled."),
    ("timeout_network", "urllib3.exceptions.ReadTimeoutError: read timed out"),
    ("timeout_network", "read tcp 10.0.0.5:443: Connection reset by peer"),
    ("timeout_network", "net/http: TLS handshake timeout"),
]

NON_MATCHING: list[tuple[str, str]] = [
    ("pytest", "Everything installed cleanly"),
    ("pytest", "collected 24 items"),
    ("pytest", "the earlier FAILED marker sits mid-line"),
    ("python_traceback", "the stack unwound without incident"),
    ("compiler", "0 problems found, compilation finished"),
    ("dependency", "Resolved 42 packages in 1.2s"),
    ("actions_runner", "Process completed with exit code 0."),
    ("oom_disk", "memory usage nominal at 412 MB"),
    ("timeout_network", "connection established in 40ms"),
]


def _matches(group: PatternGroup, line: str) -> bool:
    return any(regex.search(line) for regex in group.regexes)


def test_all_groups_are_in_priority_order() -> None:
    assert [group.name for group in ALL_GROUPS] == [
        "pytest",
        "python_traceback",
        "compiler",
        "dependency",
        "actions_runner",
        "oom_disk",
        "timeout_network",
    ]


@pytest.mark.parametrize(("group_name", "line"), MATCHING)
def test_group_matches_canonical_line(group_name: str, line: str) -> None:
    assert _matches(_BY_NAME[group_name], line)


@pytest.mark.parametrize(("group_name", "line"), NON_MATCHING)
def test_group_ignores_innocent_line(group_name: str, line: str) -> None:
    assert not _matches(_BY_NAME[group_name], line)
