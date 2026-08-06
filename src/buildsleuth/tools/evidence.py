"""Read-only views over one case's evidence.

Every tool here works from the snapshot alone, with no network, so a stored
eval case behaves exactly like a live run. Each returns text rather than
raising, because a tool error mid-conversation is worth less to the model
than a sentence explaining what went wrong.
"""

import re
from dataclasses import dataclass

MAX_LOG_LINES_PER_CALL = 200
MAX_DIFF_CHARS_PER_FILE = 4_000
NOT_FOUND = "not found"

_DIFF_HEADER_RE = re.compile(r"^diff --git a/(?P<old>\S+) b/(?P<new>\S+)$", re.MULTILINE)


@dataclass(frozen=True)
class Evidence:
    """The full log and diff for one failure, addressable by line and path."""

    log_text: str
    diff_text: str | None = None

    @property
    def log_lines(self) -> list[str]:
        return self.log_text.split("\n")

    def read_log_range(self, start_line: int, end_line: int) -> str:
        """Return cleaned log lines in an inclusive 1-based range."""
        lines = self.log_lines
        if start_line < 1 or start_line > len(lines):
            return f"{NOT_FOUND}: the log has lines 1 to {len(lines)}"

        capped_end = min(end_line, start_line + MAX_LOG_LINES_PER_CALL - 1)
        end = min(capped_end, len(lines))
        body = "\n".join(lines[start_line - 1 : end])
        # Only say truncated when the cap cut the request short. Asking past
        # the end of the log drops nothing that exists.
        truncated = " (truncated)" if capped_end < end_line else ""
        return f"[lines {start_line}-{end} of {len(lines)}{truncated}]\n{body}"

    def list_diff_files(self) -> str:
        """Paths the diff touches, which are the likeliest culprits."""
        paths = self.diff_paths()
        if not paths:
            return f"{NOT_FOUND}: this case carries no diff"
        return "\n".join(paths)

    def diff_paths(self) -> list[str]:
        if not self.diff_text:
            return []
        seen: list[str] = []
        for match in _DIFF_HEADER_RE.finditer(self.diff_text):
            path = match.group("new")
            if path not in seen:
                seen.append(path)
        return seen

    def read_diff_for_file(self, path: str) -> str:
        """The diff hunk for one path, so the model can check what changed."""
        if not self.diff_text:
            return f"{NOT_FOUND}: this case carries no diff"

        starts = [(m.start(), m.group("new")) for m in _DIFF_HEADER_RE.finditer(self.diff_text)]
        for index, (offset, candidate) in enumerate(starts):
            if candidate != path:
                continue
            end = starts[index + 1][0] if index + 1 < len(starts) else len(self.diff_text)
            hunk = self.diff_text[offset:end]
            if len(hunk) > MAX_DIFF_CHARS_PER_FILE:
                return hunk[:MAX_DIFF_CHARS_PER_FILE] + "\n...[hunk truncated]"
            return hunk
        return f"{NOT_FOUND}: {path} is not in the diff"

    def search_log(self, pattern: str, max_hits: int = 20) -> str:
        """Line numbers where a literal string appears, for targeted follow-up."""
        hits = [
            f"{number}: {line.strip()[:160]}"
            for number, line in enumerate(self.log_lines, start=1)
            if pattern in line
        ]
        if not hits:
            return f"{NOT_FOUND}: no line contains {pattern!r}"
        shown = hits[:max_hits]
        suffix = f"\n...{len(hits) - len(shown)} more matches" if len(hits) > len(shown) else ""
        return "\n".join(shown) + suffix
