"""Load and hash versioned prompt files.

A prompt file is markdown with a YAML-ish frontmatter block holding at least
an id and a version. The body is the prompt. The hash covers the body only,
so editing the changelog does not invalidate a scorecard.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

PROMPTS_ROOT = Path(__file__).parent
PROMPT_SUFFIX = ".md"
HASH_LENGTH = 12
FRONTMATTER_FENCE = "---"

_FRONTMATTER_RE = re.compile(r"\A---\n(?P<meta>.*?)\n---\n(?P<body>.*)\Z", re.DOTALL)
_META_LINE_RE = re.compile(r"^(?P<key>[a-z_]+):\s*(?P<value>.*)$")


class PromptError(Exception):
    """A prompt file is missing or malformed."""


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    text: str
    content_hash: str

    def render(self, **values: str) -> str:
        """Substitute {placeholders} in the body, failing loudly on a missing one."""
        try:
            return self.text.format(**values)
        except KeyError as error:
            raise PromptError(f"prompt {self.name} needs a value for {error}") from error


def content_hash(text: str) -> str:
    """Stable hash of prompt text, normalized so line endings cannot change it."""
    normalized = text.replace("\r\n", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:HASH_LENGTH]


def combined_hash(prompts: list[Prompt]) -> str:
    """One hash covering every active prompt, for the scorecard filename."""
    joined = "|".join(
        f"{prompt.name}:{prompt.content_hash}" for prompt in sorted(prompts, key=_key)
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:HASH_LENGTH]


def _key(prompt: Prompt) -> str:
    return prompt.name


def load_prompt(name: str, version: str = "v1", root: Path | None = None) -> Prompt:
    """Load `<root>/<name>/<version>.md`."""
    base = root if root is not None else PROMPTS_ROOT
    path = base / name / f"{version}{PROMPT_SUFFIX}"
    if not path.is_file():
        raise PromptError(f"no prompt file at {path}")

    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    match = _FRONTMATTER_RE.match(raw)
    if match is None:
        raise PromptError(f"prompt {path} is missing its {FRONTMATTER_FENCE} frontmatter block")

    meta = _parse_meta(match.group("meta"), path)
    body = match.group("body").strip()
    if not body:
        raise PromptError(f"prompt {path} has an empty body")

    return Prompt(
        name=meta.get("id", name),
        version=meta.get("version", version),
        text=body,
        content_hash=content_hash(body),
    )


def _parse_meta(block: str, path: Path) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _META_LINE_RE.match(stripped)
        if match is None:
            continue
        meta[match.group("key")] = match.group("value").strip().strip("\"'")
    if "id" not in meta:
        raise PromptError(f"prompt {path} frontmatter needs an id")
    return meta
