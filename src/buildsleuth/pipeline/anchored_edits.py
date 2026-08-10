"""Compute a unified diff from anchored edits, instead of trusting one.

Asking a model for a unified diff makes it responsible for reproducing the
lines around its change character for character, and that is where the fix
funnel loses most of its cases: four of nine measured attempts were rejected
with `patch failed` on a file the model had just been shown and had read. The
change it wanted was usually fine. The context it retyped was not, because one
lost space or one paraphrased comment is enough for git to refuse every hunk.

So the model is asked only for what it alone can supply, the old text and the
new text, and the diff is computed here. Context lines then come out of the
file itself and cannot disagree with it, which removes the failure mode rather
than repairing it after the fact the way `patch_repair` does.

Search and replace rather than a rewritten file, because a mismatch stays
detectable: an anchor either occurs in the file or it does not, and a rejection
naming the file and the reason is worth more than a plausible patch. A whole
file rewrite always applies and silently drops whatever the model forgot to
copy across.

Near misses are placed rather than refused when the file itself resolves them,
in the same spirit as `patch_repair`: an anchor typed with the wrong line
endings, with trailing whitespace, or with the indentation flattened still
identifies exactly one place in the file. Which tolerance was needed is
reported, so a run cannot quietly come to depend on the loosest one.

Line endings are the file's own throughout. Several target repositories are
CRLF from top to bottom, and a replacement typed with LF endings would produce
a patch whose every line disagrees with the checkout.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from difflib import unified_diff
from enum import StrEnum

from buildsleuth.models.triage import AnchoredEdit

CONTEXT_LINES = 3
CRLF = "\r\n"
LF = "\n"
NO_NEWLINE = "\\ No newline at end of file"
GIT_HEADER = "diff --git a/{path} b/{path}"
OLD_PREFIX = "a/"
NEW_PREFIX = "b/"
NO_EDITS = "the proposal contained no edits"
# Prefixes a model puts on a path out of diff habit, which name no directory.
PATH_NOISE = ("./", "a/", "b/")


class MatchKind(StrEnum):
    """How much tolerance placing the anchor needed. Lower down is looser."""

    EXACT = "exactly"
    RETYPED_ENDINGS = "after retyping its line endings"
    IGNORED_TRAILING_SPACE = "after ignoring trailing whitespace"
    IGNORED_INDENTATION = "after ignoring indentation"


class EditFailure(StrEnum):
    """Why an edit could not be turned into a change to a file."""

    NO_CONTENT = "no content was supplied for that file"
    EMPTY_ANCHOR = "the text to find was empty"
    NOT_FOUND = "the text to find does not occur in the file"
    AMBIGUOUS = "the text to find occurs more than once, so the edit is ambiguous"
    UNCHANGED = "the replacement is identical to the text it replaces"


@dataclass(frozen=True)
class AppliedEdit:
    """An edit that was placed, and what it took to place it."""

    path: str
    match: MatchKind


@dataclass(frozen=True)
class RejectedEdit:
    """An edit that was not placed, and why not."""

    path: str
    failure: EditFailure


@dataclass(frozen=True)
class EditResult:
    """The patch the edits amount to, plus every edit that did not land."""

    patch: str
    applied: tuple[AppliedEdit, ...] = ()
    rejected: tuple[RejectedEdit, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether every edit landed and they amount to a change.

        A partly placed proposal is not ok. Half a fix applies cleanly and
        then fails the build for a reason nobody can attribute.
        """
        return bool(self.patch) and bool(self.applied) and not self.rejected

    @property
    def report(self) -> str:
        """One line saying where every edit ended up."""
        total = len(self.applied) + len(self.rejected)
        if not total:
            return NO_EDITS
        parts = [f"placed {len(self.applied)} of {total} edits"]
        parts.extend(f"{edit.path} matched {edit.match.value}" for edit in self.applied)
        parts.extend(f"{edit.path}: {edit.failure.value}" for edit in self.rejected)
        return "; ".join(parts)


def split_lines(text: str) -> list[str]:
    """Split on newlines only, the way git reads a file into diff lines.

    Not `str.splitlines`, which also breaks on a bare carriage return and on
    several unicode separators. A file holding either would be cut into lines
    git does not agree exist, and the resulting patch would not apply.
    """
    lines = text.split(LF)
    tail = lines.pop()
    terminated = [line + LF for line in lines]
    if tail:
        terminated.append(tail)
    return terminated


def dominant_ending(text: str) -> str:
    """The line ending the file mostly uses, which is what a patch must speak."""
    crlf = text.count(CRLF)
    return CRLF if crlf and crlf >= text.count(LF) - crlf else LF


def retype_endings(text: str, ending: str) -> str:
    """Rewrite every line ending in the text to the one given."""
    return text.replace(CRLF, LF).replace(LF, ending)


def _occurrences(source: str, anchor: str) -> list[int]:
    """Every offset where the anchor occurs, including overlapping ones."""
    found: list[int] = []
    at = source.find(anchor)
    while at != -1:
        found.append(at)
        at = source.find(anchor, at + 1)
    return found


def _line_starts(lines: Sequence[str]) -> list[int]:
    starts: list[int] = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line)
    starts.append(offset)
    return starts


def _line_runs(
    source_lines: Sequence[str], anchor_lines: Sequence[str], key: Callable[[str], str]
) -> list[int]:
    """Line indexes where the anchor matches once the key is applied to both."""
    wanted = [key(line) for line in anchor_lines]
    haystack = [key(line) for line in source_lines]
    span = len(wanted)
    if not span or span > len(haystack):
        return []
    return [at for at in range(len(haystack) - span + 1) if haystack[at : at + span] == wanted]


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    kind: MatchKind


def _locate(source: str, anchor: str) -> _Span | EditFailure:
    """Find the one place the anchor refers to, loosening only as needed.

    Ambiguity stops the search rather than falling through to a looser rule,
    because a rule that ignores more can only match more.
    """
    ending = dominant_ending(source)
    for candidate, kind in (
        (anchor, MatchKind.EXACT),
        (retype_endings(anchor, ending), MatchKind.RETYPED_ENDINGS),
    ):
        hits = _occurrences(source, candidate)
        if len(hits) == 1:
            return _Span(hits[0], hits[0] + len(candidate), kind)
        if hits:
            return EditFailure.AMBIGUOUS

    source_lines = split_lines(source)
    starts = _line_starts(source_lines)
    anchor_lines = split_lines(anchor)
    tolerances: tuple[tuple[Callable[[str], str], MatchKind], ...] = (
        (str.rstrip, MatchKind.IGNORED_TRAILING_SPACE),
        (str.strip, MatchKind.IGNORED_INDENTATION),
    )
    for key, kind in tolerances:
        hits = _line_runs(source_lines, anchor_lines, key)
        if len(hits) == 1:
            at = hits[0]
            return _Span(starts[at], starts[at + len(anchor_lines)], kind)
        if hits:
            return EditFailure.AMBIGUOUS
    return EditFailure.NOT_FOUND


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _reindent(text: str, source_indent: str, anchor_indent: str) -> str:
    """Shift the replacement onto the indentation the file actually uses.

    Only reached when the anchor was placed by ignoring indentation, which
    means the model mistyped it there and has almost certainly mistyped it in
    the replacement the same way.
    """
    if source_indent == anchor_indent:
        return text
    shifted: list[str] = []
    for line in split_lines(text):
        if not line.strip() or not line.startswith(anchor_indent):
            shifted.append(line)
        else:
            shifted.append(source_indent + line[len(anchor_indent) :])
    return "".join(shifted)


def _prepared(replacement: str, source: str, span: _Span, anchor: str) -> str:
    """The replacement written the way the file writes things."""
    ending = dominant_ending(source)
    text = retype_endings(replacement, ending)
    if span.kind is MatchKind.IGNORED_INDENTATION:
        source_first = split_lines(source[span.start : span.end])[0]
        anchor_first = split_lines(anchor)[0]
        text = _reindent(text, _indent(source_first), _indent(anchor_first))
    # A whole-line anchor was matched with its terminator, so dropping it
    # would weld the replacement onto the line that follows.
    if source[span.end - 1 : span.end] == LF and not text.endswith(LF):
        text += ending
    return text


def apply_edit(source: str, edit: AnchoredEdit) -> tuple[str, MatchKind] | EditFailure:
    """Place one edit in the text, or say why it could not be placed."""
    if not edit.find:
        return EditFailure.EMPTY_ANCHOR
    span = _locate(source, edit.find)
    if isinstance(span, EditFailure):
        return span
    replacement = _prepared(edit.replace, source, span, edit.find)
    updated = source[: span.start] + replacement + source[span.end :]
    if updated == source:
        return EditFailure.UNCHANGED
    return updated, span.kind


def resolve_path(path: str, contents: Mapping[str, str]) -> str | None:
    """The file the edit means, spelled the way the checkout spells it.

    A model that has just been shown a diff writes `a/src/app.py` about half
    the time, and one working from a subdirectory drops the leading segments.
    Neither changes which file is meant, and the alternative is rejecting an
    otherwise correct edit over a prefix. The key that comes back is always
    one of the keys supplied, so the patch header names a real path.
    """
    if path in contents:
        return path
    wanted = path.replace("\\", "/")
    for prefix in PATH_NOISE:
        wanted = wanted.removeprefix(prefix)
    matches = [
        key
        for key in contents
        if key.replace("\\", "/") == wanted or key.replace("\\", "/").endswith("/" + wanted)
    ]
    return matches[0] if len(matches) == 1 else None


def unified_patch(path: str, before: str, after: str) -> str:
    """A unified diff of two versions of one file, or nothing if they agree.

    Every line comes from one of the two texts, so the context is the file's
    own and their line endings survive intact.
    """
    if before == after:
        return ""
    body = unified_diff(
        split_lines(before),
        split_lines(after),
        fromfile=OLD_PREFIX + path,
        tofile=NEW_PREFIX + path,
        n=CONTEXT_LINES,
    )
    return GIT_HEADER.format(path=path) + LF + "".join(_terminated(line) for line in body)


def _terminated(line: str) -> str:
    """Close a diff line, flagging the one case where the file has no newline."""
    if line.endswith(LF):
        return line
    return f"{line}{LF}{NO_NEWLINE}{LF}"


def patch_from_edits(edits: Sequence[AnchoredEdit], contents: Mapping[str, str]) -> EditResult:
    """Turn anchored edits into one patch, reporting every edit that missed.

    Edits are placed in order against the text as previous edits left it, so
    two edits to one file behave the way an editor would rather than fighting
    over offsets into the original.
    """
    updated: dict[str, str] = {}
    order: list[str] = []
    applied: list[AppliedEdit] = []
    rejected: list[RejectedEdit] = []

    for edit in edits:
        path = resolve_path(edit.path, contents)
        if path is None:
            rejected.append(RejectedEdit(edit.path, EditFailure.NO_CONTENT))
            continue
        outcome = apply_edit(updated.get(path, contents[path]), edit)
        if isinstance(outcome, EditFailure):
            rejected.append(RejectedEdit(path, outcome))
            continue
        text, kind = outcome
        updated[path] = text
        applied.append(AppliedEdit(path, kind))
        if path not in order:
            order.append(path)

    patch = "".join(unified_patch(path, contents[path], updated[path]) for path in order)
    return EditResult(patch=patch, applied=tuple(applied), rejected=tuple(rejected))
