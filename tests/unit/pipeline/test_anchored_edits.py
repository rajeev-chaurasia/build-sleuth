"""Tests for computing a patch from anchored edits.

The failure being removed is real and measured: four of the nine attempts in
`results/fix-funnel.json` were rejected with `patch failed` because the model
retyped the lines around its change and got them subtly wrong. Nothing here
may reintroduce that, so the guarantees tested are that context comes from the
file, that line endings are the file's own, and that an anchor which does not
fit the file is reported rather than guessed at.

Several tests hand the result to a real git, because git is the only authority
on whether a patch applies.
"""

import subprocess
from pathlib import Path

import pytest

from buildsleuth.models.triage import AnchoredEdit, EditProposal
from buildsleuth.pipeline.anchored_edits import (
    NO_EDITS,
    EditFailure,
    MatchKind,
    apply_edit,
    dominant_ending,
    patch_from_edits,
    resolve_path,
    split_lines,
    unified_patch,
)

PATH = "app.py"
SOURCE = (
    "import re\n"
    "\n"
    "\n"
    "def compile_pattern(text):\n"
    "    # match from the start of the line\n"
    '    return re.compile(r"^" + text)\n'
    "\n"
    "\n"
    "def other(text):\n"
    "    return text\n"
)
ANCHOR = '    return re.compile(r"^" + text)\n'
REPLACEMENT = '    return re.compile(r"^" + text, re.MULTILINE)\n'
COMMENT = "    # match from the start of the line\n"
# Both lines with their indentation flattened, which no longer occurs in the
# file as written and can only be placed by ignoring indentation.
FLAT_ANCHOR = COMMENT.lstrip(" ") + ANCHOR.lstrip(" ")
FLAT_REPLACEMENT = COMMENT.lstrip(" ") + REPLACEMENT.lstrip(" ")
# The same file under a path the model has to spell to reach it.
NESTED = {"src/app.py": SOURCE}


def _edit(find: str = ANCHOR, replace: str = REPLACEMENT, path: str = PATH) -> AnchoredEdit:
    return AnchoredEdit(path=path, find=find, replace=replace)


def _applied(source: str = SOURCE, **kwargs: str) -> str:
    outcome = apply_edit(source, _edit(**kwargs))
    assert not isinstance(outcome, EditFailure), outcome
    return outcome[0]


def _kind(source: str = SOURCE, **kwargs: str) -> MatchKind:
    outcome = apply_edit(source, _edit(**kwargs))
    assert not isinstance(outcome, EditFailure), outcome
    return outcome[1]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repository, since only git can say whether a patch applies."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".git" / "config").write_text(
        "[core]\n\tautocrlf = false\n", encoding="utf-8", newline="\n"
    )
    return tmp_path


def _accepts(repo: Path, contents: dict[str, str], patch: str) -> bool:
    """Whether git will apply the patch to a checkout holding those files."""
    for path, source in contents.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        # Bytes, because writing text on Windows would rewrite the endings the
        # patch was computed against.
        target.write_bytes(source.encode("utf-8"))
    completed = subprocess.run(
        ["git", "apply", "--check", "--verbose", "-"],
        cwd=repo,
        input=patch.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


class TestPlacesTheAnchor:
    def test_an_exact_anchor_is_replaced_where_it_sits(self) -> None:
        updated = _applied()
        assert REPLACEMENT in updated
        assert ANCHOR not in updated

    def test_an_exact_match_is_reported_as_exact(self) -> None:
        assert _kind() is MatchKind.EXACT

    def test_the_rest_of_the_file_is_untouched(self) -> None:
        updated = _applied()
        assert updated.replace(REPLACEMENT, ANCHOR) == SOURCE

    def test_a_multi_line_anchor_is_replaced_as_one_block(self) -> None:
        updated = _applied(find=COMMENT + ANCHOR, replace=REPLACEMENT)
        assert "# match from the start of the line" not in updated
        assert REPLACEMENT in updated

    def test_an_anchor_may_add_lines(self) -> None:
        updated = _applied(replace="    text = text.strip()\n" + ANCHOR)
        assert "    text = text.strip()\n" in updated
        assert ANCHOR in updated

    def test_an_anchor_may_delete_lines(self) -> None:
        updated = _applied(find=COMMENT, replace="")
        assert "# match from the start" not in updated
        assert ANCHOR in updated


class TestReportsWhatItCannotPlace:
    def test_an_anchor_that_is_not_in_the_file_is_reported(self) -> None:
        assert apply_edit(SOURCE, _edit(find="def absent():\n")) is EditFailure.NOT_FOUND

    def test_a_paraphrased_comment_is_reported_rather_than_guessed_at(self) -> None:
        # The measured failure: the model rewords a line it was told to copy.
        # Under a unified diff this silently produced a patch git refused.
        reworded = "    # match at the beginning of the line\n" + ANCHOR
        assert apply_edit(SOURCE, _edit(find=reworded)) is EditFailure.NOT_FOUND

    def test_an_anchor_occurring_twice_is_refused_as_ambiguous(self) -> None:
        assert apply_edit(SOURCE, _edit(find="\n\n")) is EditFailure.AMBIGUOUS

    def test_ambiguity_is_not_resolved_by_loosening_the_match(self) -> None:
        # A looser rule can only match more places, so falling through to one
        # would pick an arbitrary occurrence.
        source = "    value = 1\n    other = 2\n    value = 1\n"
        assert apply_edit(source, _edit(find="value = 1\n")) is EditFailure.AMBIGUOUS

    def test_an_empty_anchor_is_refused(self) -> None:
        assert apply_edit(SOURCE, _edit(find="")) is EditFailure.EMPTY_ANCHOR

    def test_a_replacement_identical_to_the_anchor_is_refused(self) -> None:
        assert apply_edit(SOURCE, _edit(replace=ANCHOR)) is EditFailure.UNCHANGED

    def test_an_ambiguous_path_is_refused_rather_than_picked(self) -> None:
        contents = {"src/app.py": SOURCE, "tests/app.py": SOURCE}
        assert resolve_path("app.py", contents) is None

    def test_an_edit_to_a_file_with_no_content_is_refused(self) -> None:
        result = patch_from_edits([_edit(path="unseen.py")], {PATH: SOURCE})
        assert result.rejected == ((result.rejected[0]),)
        assert result.rejected[0].failure is EditFailure.NO_CONTENT
        assert result.patch == ""
        assert not result.ok


class TestToleratesNearMisses:
    """The whitespace a model loses is recoverable from the file itself, in
    the same spirit as recomputing a hunk header rather than trusting one."""

    def test_an_anchor_typed_with_the_wrong_line_endings_still_lands(self) -> None:
        crlf = SOURCE.replace("\n", "\r\n")
        assert _kind(crlf, find=ANCHOR) is MatchKind.RETYPED_ENDINGS

    def test_trailing_whitespace_on_the_anchor_is_forgiven(self) -> None:
        assert _kind(find=ANCHOR.replace("\n", "   \n")) is MatchKind.IGNORED_TRAILING_SPACE

    def test_flattened_indentation_is_forgiven(self) -> None:
        assert _kind(find=FLAT_ANCHOR) is MatchKind.IGNORED_INDENTATION

    def test_a_forgiven_indent_is_restored_on_the_replacement(self) -> None:
        # The model that mistyped the indent in the anchor mistyped it in the
        # replacement too, and Python would not survive that.
        updated = _applied(find=FLAT_ANCHOR, replace=FLAT_REPLACEMENT)
        assert REPLACEMENT in updated
        assert COMMENT in updated

    def test_a_blank_line_in_the_replacement_is_not_given_indentation(self) -> None:
        # Indenting a blank line would leave trailing whitespace the project
        # never had, on a line the fix did not touch.
        replace = FLAT_REPLACEMENT.replace(COMMENT.lstrip(" "), "\n")
        assert "\n\n" in _applied(find=FLAT_ANCHOR, replace=replace)

    def test_the_replacement_is_left_alone_when_only_a_later_line_was_mistyped(self) -> None:
        source = "def f():\n    a = 1\n        b = 2\n"
        find = "    a = 1\n    b = 2\n"
        outcome = apply_edit(source, _edit(find=find, replace="    a = 3\n    b = 4\n"))
        assert outcome == ("def f():\n    a = 3\n    b = 4\n", MatchKind.IGNORED_INDENTATION)

    def test_a_tolerance_that_matches_two_places_is_still_ambiguous(self) -> None:
        source = "if a:\n    value = 1\nif b:\n        value = 1\n"
        assert apply_edit(source, _edit(find="\tvalue = 1\n")) is EditFailure.AMBIGUOUS

    def test_an_anchor_longer_than_the_file_is_not_found(self) -> None:
        assert apply_edit("x = 1\n", _edit(find="a\nb\nc\n")) is EditFailure.NOT_FOUND

    def test_a_replacement_typed_with_lf_takes_the_file_endings(self) -> None:
        crlf = SOURCE.replace("\n", "\r\n")
        updated = _applied(crlf, find=ANCHOR, replace=REPLACEMENT)
        assert REPLACEMENT.replace("\n", "\r\n") in updated
        assert "\n" not in updated.replace("\r\n", "")

    def test_a_replacement_missing_its_final_newline_does_not_weld_two_lines(self) -> None:
        updated = _applied(replace=REPLACEMENT.rstrip("\n"))
        assert REPLACEMENT in updated

    def test_the_exact_match_is_preferred_when_both_would_work(self) -> None:
        assert _kind() is MatchKind.EXACT


class TestResolvesThePath:
    """The file is right and the spelling is not, which is not worth losing an
    edit over. Whatever comes back is one of the keys that were supplied, so
    the patch header can only ever name a file the checkout has."""

    def test_the_exact_path_wins(self) -> None:
        assert resolve_path("src/app.py", NESTED) == "src/app.py"

    @pytest.mark.parametrize("spelling", ["a/src/app.py", "b/src/app.py", "./src/app.py"])
    def test_a_diff_prefix_is_ignored(self, spelling: str) -> None:
        assert resolve_path(spelling, NESTED) == "src/app.py"

    def test_a_path_relative_to_a_subdirectory_still_resolves(self) -> None:
        assert resolve_path("app.py", NESTED) == "src/app.py"

    def test_windows_separators_resolve(self) -> None:
        assert resolve_path("src\\app.py", NESTED) == "src/app.py"

    def test_a_file_that_was_never_supplied_does_not_resolve(self) -> None:
        assert resolve_path("src/other.py", NESTED) is None

    def test_a_partial_name_does_not_resolve(self) -> None:
        # Suffix matching stops at a directory boundary, so `pp.py` is not
        # quietly taken to mean `app.py`.
        assert resolve_path("pp.py", NESTED) is None

    def test_the_patch_names_the_path_the_checkout_uses(self) -> None:
        result = patch_from_edits([_edit(path="a/src/app.py")], NESTED)
        assert result.ok
        assert "a/a/src/app.py" not in result.patch
        assert result.patch.startswith("diff --git a/src/app.py b/src/app.py\n")
        assert result.applied[0].path == "src/app.py"


class TestBuildsAPatchGitAccepts:
    def test_the_patch_applies_to_the_file_it_was_computed_from(self, repo: Path) -> None:
        result = patch_from_edits([_edit()], {PATH: SOURCE})
        assert result.ok
        assert _accepts(repo, {PATH: SOURCE}, result.patch)

    def test_the_patch_applies_to_a_crlf_checkout(self, repo: Path) -> None:
        # A patch whose endings disagree has every hunk rejected, which is
        # what made this worth doing deterministically.
        crlf = SOURCE.replace("\n", "\r\n")
        result = patch_from_edits([_edit()], {PATH: crlf})
        assert result.ok
        assert _accepts(repo, {PATH: crlf}, result.patch)

    def test_a_file_with_no_trailing_newline_still_applies(self, repo: Path) -> None:
        source = SOURCE.rstrip("\n")
        result = patch_from_edits(
            [_edit(find="    return text", replace="    return text.strip()")], {PATH: source}
        )
        assert result.ok
        assert "\\ No newline at end of file" in result.patch
        assert _accepts(repo, {PATH: source}, result.patch)

    def test_a_patch_from_a_loosely_matched_anchor_still_applies(self, repo: Path) -> None:
        result = patch_from_edits(
            [_edit(find=FLAT_ANCHOR, replace=FLAT_REPLACEMENT)], {PATH: SOURCE}
        )
        assert result.ok
        assert _accepts(repo, {PATH: SOURCE}, result.patch)

    def test_the_patch_names_the_file_the_way_git_expects(self) -> None:
        patch = patch_from_edits([_edit()], {PATH: SOURCE}).patch
        assert patch.startswith(f"diff --git a/{PATH} b/{PATH}\n")
        assert f"--- a/{PATH}\n" in patch
        assert f"+++ b/{PATH}\n" in patch

    def test_context_lines_are_the_files_own(self) -> None:
        patch = patch_from_edits([_edit()], {PATH: SOURCE}).patch
        assert "     # match from the start of the line\n" in patch
        assert f"-{ANCHOR}" in patch
        assert f"+{REPLACEMENT}" in patch

    def test_two_edits_to_one_file_produce_one_file_section(self, repo: Path) -> None:
        result = patch_from_edits(
            [_edit(), _edit(find="    return text\n", replace="    return text.strip()\n")],
            {PATH: SOURCE},
        )
        assert result.patch.count("diff --git") == 1
        assert len(result.applied) == 2
        assert _accepts(repo, {PATH: SOURCE}, result.patch)

    def test_edits_to_two_files_produce_a_section_each(self, repo: Path) -> None:
        other = "value = 1\n"
        result = patch_from_edits(
            [_edit(), _edit(path="other.py", find=other, replace="value = 2\n")],
            {PATH: SOURCE, "other.py": other},
        )
        assert result.patch.count("diff --git") == 2
        assert _accepts(repo, {PATH: SOURCE, "other.py": other}, result.patch)

    def test_a_second_edit_sees_what_the_first_one_did(self) -> None:
        result = patch_from_edits(
            [_edit(), _edit(find=REPLACEMENT, replace="    return None\n")], {PATH: SOURCE}
        )
        assert len(result.applied) == 2
        assert "+    return None\n" in result.patch
        assert REPLACEMENT not in result.patch.replace(f"-{REPLACEMENT}", "")


class TestRefusesToHalfApply:
    def test_a_rejected_edit_makes_the_whole_result_not_ok(self) -> None:
        result = patch_from_edits([_edit(), _edit(find="def absent():\n")], {PATH: SOURCE})
        assert result.applied
        assert result.rejected
        assert not result.ok

    def test_an_empty_proposal_is_not_ok_and_says_so(self) -> None:
        result = patch_from_edits([], {PATH: SOURCE})
        assert not result.ok
        assert result.report == NO_EDITS
        assert result.patch == ""

    def test_the_report_names_the_file_and_the_reason(self) -> None:
        result = patch_from_edits([_edit(find="def absent():\n")], {PATH: SOURCE})
        assert PATH in result.report
        assert EditFailure.NOT_FOUND.value in result.report
        assert "placed 0 of 1 edits" in result.report

    def test_the_report_says_which_tolerance_was_needed(self) -> None:
        result = patch_from_edits([_edit(find=FLAT_ANCHOR)], {PATH: SOURCE})
        assert MatchKind.IGNORED_INDENTATION.value in result.report
        assert result.ok


class TestLinePrimitives:
    def test_lines_split_on_newlines_only(self) -> None:
        # str.splitlines would break on the bare carriage return and produce
        # lines git does not agree exist.
        assert split_lines("a\rb\nc") == ["a\rb\n", "c"]

    def test_a_trailing_newline_does_not_add_an_empty_line(self) -> None:
        assert split_lines("a\n") == ["a\n"]

    def test_empty_text_has_no_lines(self) -> None:
        assert split_lines("") == []

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("a\r\nb\r\n", "\r\n"),
            ("a\nb\n", "\n"),
            ("a\r\nb\nc\nd\n", "\n"),
            ("no endings at all", "\n"),
        ],
    )
    def test_the_dominant_ending_is_the_one_the_file_mostly_uses(
        self, text: str, expected: str
    ) -> None:
        assert dominant_ending(text) == expected

    def test_identical_texts_produce_no_patch(self) -> None:
        assert unified_patch(PATH, SOURCE, SOURCE) == ""


class TestProposalModel:
    def test_a_proposal_with_no_edits_is_empty(self) -> None:
        assert EditProposal().is_empty is True

    def test_a_proposal_with_an_edit_is_not_empty(self) -> None:
        assert EditProposal(edits=[_edit()]).is_empty is False

    def test_touched_files_are_listed_once_in_order(self) -> None:
        proposal = EditProposal(
            edits=[_edit(), _edit(path="other.py"), _edit(find="    return text\n")]
        )
        assert proposal.touched_files == [PATH, "other.py"]
