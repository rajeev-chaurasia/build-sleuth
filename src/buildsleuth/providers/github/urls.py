"""Parsing of GitHub Actions run URLs into provider-independent run refs."""

import re

from buildsleuth.models.run import RunRef

_RUN_URL_RE = re.compile(
    r"https://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)"
    r"/actions/runs/(?P<run_id>\d+)"
    r"(?:/attempts/(?P<attempt>\d+))?"
    r"(?:/job/\d+)?"
    r"/?"
    r"(?:[?#]\S*)?"  # tolerate query/fragment on links copied from the UI
)

_EXPECTED_FORMS = (
    "https://github.com/{owner}/{repo}/actions/runs/{run_id}[/attempts/{attempt}][/job/{job_id}]"
)


def parse_run_url(url: str) -> RunRef:
    """Parse a GitHub Actions run URL into a RunRef.

    Job URLs are accepted too; they still resolve to the run they belong to,
    since the run id is part of the path.
    """
    match = _RUN_URL_RE.fullmatch(url.strip())
    if match is None:
        raise ValueError(
            f"Not a recognized GitHub Actions run URL: {url!r}. Expected {_EXPECTED_FORMS}"
        )
    attempt = match["attempt"]
    return RunRef(
        repo=f"{match['owner']}/{match['repo']}",
        run_id=int(match["run_id"]),
        attempt=int(attempt) if attempt is not None else None,
    )
