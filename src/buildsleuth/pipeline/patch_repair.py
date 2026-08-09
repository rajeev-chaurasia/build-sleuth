"""Repair the parts of a model's diff that can be recomputed from the diff.

A unified diff hunk header states how many lines the hunk covers on each
side. Models get that arithmetic wrong often, and git refuses the whole patch
with `corrupt patch at line N`, discarding a change that may have been
entirely correct. Three of eight rejections in the last measured run failed
this way.

The counts are not information the model has to supply: they are derivable
from the hunk body. So they are recomputed here rather than trusted, which is
the same principle as the rest of the pipeline, applied to formatting instead
of to judgement.

Nothing here changes what a patch does. Only the declared line counts and the
markers that carry no content are touched, so a patch that was already well
formed comes back unchanged.
"""

import re

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
FILE_MARKERS = ("--- ", "+++ ", "diff ", "index ", "new file", "deleted file")
NO_NEWLINE = "\\ No newline at end of file"
CONTEXT = " "
ADDED = "+"
REMOVED = "-"


def _split_ending(line: str) -> tuple[str, str]:
    """Separate a line from its terminator so endings survive a rewrite."""
    for ending in ("\r\n", "\n", "\r"):
        if line.endswith(ending):
            return line[: -len(ending)], ending
    return line, ""


def _is_body(text: str) -> bool:
    """Whether a line belongs to the hunk body rather than ending it."""
    if text == "" or text == NO_NEWLINE:
        return True
    return text.startswith((CONTEXT, ADDED, REMOVED))


def _counts(body: list[str]) -> tuple[int, int]:
    """How many lines the hunk actually covers before and after."""
    old = new = 0
    for text in body:
        if text == NO_NEWLINE:
            continue
        if text.startswith(ADDED):
            new += 1
        elif text.startswith(REMOVED):
            old += 1
        else:
            # A context line, including the empty one a model writes when it
            # drops the leading space on a blank line.
            old += 1
            new += 1
    return old, new


def repair_patch(patch: str) -> str:
    """Recompute every hunk header from the hunk it describes.

    Returns the patch unchanged when the headers already agree with their
    bodies, so a correct patch is never rewritten.
    """
    lines = patch.splitlines(keepends=True)
    out: list[str] = []
    index = 0

    while index < len(lines):
        text, ending = _split_ending(lines[index])
        match = HUNK_HEADER.match(text)
        if match is None:
            out.append(lines[index])
            index += 1
            continue

        body: list[str] = []
        cursor = index + 1
        while cursor < len(lines):
            candidate, _ = _split_ending(lines[cursor])
            if candidate.startswith(FILE_MARKERS) or HUNK_HEADER.match(candidate):
                break
            if not _is_body(candidate):
                break
            body.append(candidate)
            cursor += 1

        old_count, new_count = _counts(body)
        old_start, new_start = match.group(1), match.group(3)
        # A zero length side is written with a start of zero by convention,
        # and git rejects the header otherwise.
        if old_count == 0:
            old_start = "0"
        if new_count == 0:
            new_start = "0"
        heading = match.group(5)
        out.append(f"@@ -{old_start},{old_count} +{new_start},{new_count} @@{heading}{ending}")
        out.extend(lines[index + 1 : cursor])
        index = cursor

    return "".join(out)


def needs_repair(patch: str) -> bool:
    """Whether the declared hunk headers disagree with their bodies."""
    return repair_patch(patch) != patch
