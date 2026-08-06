"""Pure text cleanup for raw CI logs."""

import re

# GitHub prefixes every log line with an ISO-8601 UTC timestamp and a space;
# the fractional-second digits vary in count.
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z ", re.MULTILINE)
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# Progress bars rewrite a line with a lone carriage return. Left in place these
# would make str.splitlines disagree with a split on "\n", so line numbers
# reported by the condenser would not match the cleaned text.
_LINE_BREAK_RE = re.compile(r"\r\n?")


def normalize_line_breaks(text: str) -> str:
    """Convert CRLF and lone CR to LF so line numbering is unambiguous."""
    return _LINE_BREAK_RE.sub("\n", text)


def strip_timestamps(text: str) -> str:
    """Remove the ISO-8601 UTC timestamp prefix GitHub adds at the start of each line."""
    return _TIMESTAMP_RE.sub("", text)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences."""
    return _ANSI_RE.sub("", text)


def clean_log(text: str) -> str:
    """Normalize line breaks, then strip timestamps and ANSI escape sequences."""
    return strip_ansi(strip_timestamps(normalize_line_breaks(text)))
