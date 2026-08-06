"""Pure text cleanup for raw CI logs."""

import re

# GitHub prefixes every log line with an ISO-8601 UTC timestamp and a space;
# the fractional-second digits vary in count.
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z ", re.MULTILINE)
# CSI sequences (colors, cursor moves) and OSC sequences (terminal titles),
# which CI tooling emits and which would otherwise survive into excerpts.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# GitHub prefixes downloaded logs with a UTF-8 byte order mark. Left in place
# it sits before the first timestamp and defeats the line-anchored strip.
_BOM = "﻿"
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
    """Remove ANSI CSI and OSC escape sequences."""
    return _ANSI_RE.sub("", text)


def strip_bom(text: str) -> str:
    """Remove a leading byte order mark."""
    return text.removeprefix(_BOM)


def clean_log(text: str) -> str:
    """Normalize a raw CI log into plain text with stable line numbering."""
    return strip_ansi(strip_timestamps(normalize_line_breaks(strip_bom(text))))
