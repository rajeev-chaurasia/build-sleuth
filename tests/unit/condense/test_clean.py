"""Tests for pure log cleanup functions."""

from buildsleuth.condense.clean import clean_log, strip_ansi, strip_timestamps


def test_strip_timestamps_removes_line_start_prefixes() -> None:
    raw = (
        "2026-08-05T10:31:44.1234567Z first line\n"
        "2026-08-05T10:31:45.12Z fraction digits vary\n"
        "2026-08-05T10:31:46Z no fraction at all\n"
    )
    assert strip_timestamps(raw) == "first line\nfraction digits vary\nno fraction at all\n"


def test_strip_timestamps_leaves_mid_line_timestamps() -> None:
    line = "retry scheduled for 2026-08-05T10:31:44.123Z by the queue\n"
    assert strip_timestamps(line) == line


def test_strip_timestamps_handles_blank_log_lines() -> None:
    assert strip_timestamps("2026-08-05T10:31:44.1234567Z \n") == "\n"


def test_strip_ansi_removes_escape_sequences() -> None:
    colored = "\x1b[31mred\x1b[0m plain \x1b[1;32mbold green\x1b[0m\x1b[K"
    assert strip_ansi(colored) == "red plain bold green"


def test_clean_log_normalizes_crlf_then_strips() -> None:
    raw = "2026-08-05T10:31:44.1234567Z \x1b[31mboom\x1b[0m\r\n2026-08-05T10:31:45.99Z next\r\n"
    assert clean_log(raw) == "boom\nnext\n"


def test_clean_log_is_idempotent() -> None:
    raw = "2026-08-05T10:31:44.1234567Z \x1b[31mboom\x1b[0m\r\nplain line\n"
    once = clean_log(raw)
    assert clean_log(once) == once
