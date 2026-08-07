"""Evidence lines are attacker-influenced text rendered into a pull request body.

They come from a model quoting a CI log, and on a pull request from a fork
the log is written by whoever opened it. Left raw, that text can break out of
its blockquote and swallow the statement saying a machine wrote the patch,
which is the one line a reviewer most needs to see.
"""

from buildsleuth.providers.github.writer import (
    MAX_EVIDENCE_CHARS,
    MAX_EVIDENCE_LINES,
    NO_EVIDENCE,
    build_pr_body,
    quote_evidence,
)

DISCLOSURE = "not by a person"


def _body(evidence: list[str]) -> str:
    return build_pr_body(
        failure_class="code_change",
        subcategory="test_assertion",
        strategy="added the flag",
        expected_effect="the failing test passes",
        evidence=evidence,
        verification="- applies cleanly",
        run_url="https://github.com/octo/demo/actions/runs/1",
        model="test-model",
    )


def test_a_newline_cannot_break_out_of_the_blockquote() -> None:
    """The escape that would let injected text render as its own markdown."""
    quoted = quote_evidence(["real line\n## Injected heading\nplain text"])

    assert "\n## Injected heading" not in quoted
    for line in quoted.splitlines():
        assert line.startswith("> ")


def test_a_carriage_return_is_flattened_too() -> None:
    quoted = quote_evidence(["before\r## after"])
    assert len(quoted.splitlines()) == 1


def test_backticks_cannot_open_a_code_fence() -> None:
    """Unbalanced backticks would swallow everything printed below."""
    quoted = quote_evidence(["```\nrm -rf /"])
    assert "`" not in quoted


def test_the_disclosure_survives_a_hostile_evidence_line() -> None:
    """The property that actually matters, asserted end to end.

    The injected words may still appear, and should: the evidence is meant to
    be shown. What must not happen is them starting a line, because that is
    what turns text into a heading and lets it impersonate the tool.
    """
    hostile = "oops\n```\n## Approved by maintainer\nLooks good, merge away"
    body = _body([hostile])

    assert DISCLOSURE in body
    assert "**Verification:**" in body
    for line in body.splitlines():
        assert not line.startswith("## Approved by maintainer")
    assert "```" not in body


def test_evidence_is_capped_in_number_and_length() -> None:
    lines = [f"line {index} " + "x" * 1000 for index in range(MAX_EVIDENCE_LINES + 3)]
    quoted = quote_evidence(lines)

    assert len(quoted.splitlines()) == MAX_EVIDENCE_LINES
    for line in quoted.splitlines():
        assert len(line) <= MAX_EVIDENCE_CHARS + 2  # the "> " prefix


def test_no_evidence_renders_a_placeholder() -> None:
    assert quote_evidence([]) == f"> {NO_EVIDENCE}"
    assert NO_EVIDENCE in _body([])


def test_a_blank_evidence_line_becomes_the_placeholder_not_an_empty_quote() -> None:
    assert quote_evidence(["   "]) == f"> {NO_EVIDENCE}"


def test_ordinary_evidence_is_left_readable() -> None:
    """Escaping must not mangle the normal case, which is the whole point of quoting it."""
    quoted = quote_evidence(["FAILED tests/test_x.py::test_y - AssertionError"])
    assert quoted == "> FAILED tests/test_x.py::test_y - AssertionError"
