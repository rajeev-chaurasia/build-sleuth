"""Registry of error signature regex groups, ordered by priority.

Patterns are matched per line against the cleaned log. Case-sensitive on
purpose: the canonical tool output is stable and lowering the case would
invite false positives from prose.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PatternGroup:
    """A named set of compiled regexes that identify one failure family."""

    name: str
    regexes: tuple[re.Pattern[str], ...]


def _compile(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p) for p in patterns)


PYTEST = PatternGroup(
    name="pytest",
    regexes=_compile(
        r"^FAILED ",
        r"^ERROR ",
        r"^E\s+",
        r"=+ (FAILURES|ERRORS) =+",
        r"AssertionError",
    ),
)

PYTHON_TRACEBACK = PatternGroup(
    name="python_traceback",
    regexes=_compile(r"Traceback \(most recent call last\)"),
)

COMPILER = PatternGroup(
    name="compiler",
    regexes=_compile(
        r"error(\[\w+\])?:",
        r": error ",
        r"SyntaxError",
        r"TypeError:",
    ),
)

DEPENDENCY = PatternGroup(
    name="dependency",
    regexes=_compile(
        r"Could not find a version",
        r"ResolutionImpossible",
        r"npm ERR!",
        r"ERESOLVE",
        r"404 Not Found",
        r"ETIMEDOUT",
    ),
)

ACTIONS_RUNNER = PatternGroup(
    name="actions_runner",
    regexes=_compile(
        r"##\[error\]",
        r"Process completed with exit code [1-9]",
    ),
)

OOM_DISK = PatternGroup(
    name="oom_disk",
    regexes=_compile(
        r"OutOfMemoryError",
        r"(?<!\w)Killed(?!\w)",
        r"No space left on device",
        r"MemoryError",
    ),
)

TIMEOUT_NETWORK = PatternGroup(
    name="timeout_network",
    regexes=_compile(
        r"The operation was canceled",
        r"timed out",
        r"Connection reset",
        r"TLS handshake (?:timeout|failure|error)",
    ),
)

ALL_GROUPS: tuple[PatternGroup, ...] = (
    PYTEST,
    PYTHON_TRACEBACK,
    COMPILER,
    DEPENDENCY,
    ACTIONS_RUNNER,
    OOM_DISK,
    TIMEOUT_NETWORK,
)
