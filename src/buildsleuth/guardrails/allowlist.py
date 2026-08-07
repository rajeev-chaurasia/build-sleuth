"""Which repositories this agent may write to.

The allowlist is empty by default, so a fresh checkout cannot open a pull
request anywhere no matter what the rest of the code decides. Opting a
repository in is a deliberate act by whoever runs it, not a default.
"""

from buildsleuth.config import Settings

REPO_SEPARATOR = ","


class GuardrailViolation(Exception):
    """A write was attempted against a repository nobody allowed."""


def allowed_repos(settings: Settings) -> frozenset[str]:
    raw = settings.pr_allowlist or ""
    return frozenset(name.strip().lower() for name in raw.split(REPO_SEPARATOR) if name.strip())


def check_write_target(repo: str, settings: Settings) -> None:
    """Raise unless this repository was explicitly opted in.

    Raises rather than warns on purpose. A warning is something a script
    ignores, and the thing being guarded is writing to somebody's repository.
    """
    allowed = allowed_repos(settings)
    if not allowed:
        raise GuardrailViolation(
            f"refusing to write to {repo}: no repository is allowed. Set"
            " BUILDSLEUTH_PR_ALLOWLIST to the repositories this may open pull requests against."
        )
    if repo.strip().lower() not in allowed:
        listed = ", ".join(sorted(allowed))
        raise GuardrailViolation(
            f"refusing to write to {repo}: it is not in the allowlist ({listed})"
        )
