"""Thin httpx wrapper over the GitHub REST API.

One method per endpoint, returning parsed JSON, text, or bytes; mapping into
domain models lives in provider.py. Log endpoints answer with a 302 to a
signed URL that expires in about a minute, so the client always follows
redirects immediately.
"""

import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

DEFAULT_BASE_URL = "https://api.github.com"
API_VERSION = "2022-11-28"
ACCEPT_JSON = "application/vnd.github+json"
ACCEPT_DIFF = "application/vnd.github.diff"
ACCEPT_RAW = "application/vnd.github.raw+json"
JOBS_PER_PAGE = 100
MAX_RETRIES = 2
MAX_RETRY_SLEEP_SECONDS = 30.0
SERVER_ERROR_RETRY_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 30.0
RATE_LIMIT_STATUSES = frozenset({httpx.codes.FORBIDDEN, httpx.codes.TOO_MANY_REQUESTS})
SERVER_ERROR_STATUSES = frozenset(
    {httpx.codes.BAD_GATEWAY, httpx.codes.SERVICE_UNAVAILABLE, httpx.codes.GATEWAY_TIMEOUT}
)
ERROR_PREVIEW_CHARS = 200

JsonDict = dict[str, Any]


def _redact_url(url: str) -> str:
    """Drop the query string so signed-URL signatures never reach logs or errors."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class GitHubApiError(Exception):
    """Raised for a non-2xx GitHub API response."""

    def __init__(self, status: int, message: str, url: str) -> None:
        super().__init__(f"GitHub API returned {status} for {url}: {message}")
        self.status = status
        self.message = message
        self.url = url


class GitHubClient:
    """Authenticated GitHub REST client with rate-limit retries."""

    def __init__(self, token: str | None, base_url: str = DEFAULT_BASE_URL) -> None:
        self._base_host = urlsplit(base_url).netloc
        headers = {"Accept": ACCEPT_JSON, "X-GitHub-Api-Version": API_VERSION}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(
            base_url=base_url,
            headers=headers,
            follow_redirects=True,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._http.close()

    def get_run(self, repo: str, run_id: int) -> JsonDict:
        """Fetch one workflow run."""
        data: JsonDict = self._get(f"/repos/{repo}/actions/runs/{run_id}").json()
        return data

    def get_jobs(self, repo: str, run_id: int) -> list[JsonDict]:
        """Fetch all jobs of a run, following Link header pagination."""
        next_url: str | None = f"/repos/{repo}/actions/runs/{run_id}/jobs?per_page={JOBS_PER_PAGE}"
        jobs: list[JsonDict] = []
        while next_url is not None:
            response = self._get(next_url)
            jobs.extend(response.json()["jobs"])
            next_link = response.links.get("next")
            next_url = self._same_host_url(next_link["url"]) if next_link else None
        return jobs

    def _same_host_url(self, url: str) -> str:
        """Guard against a Link header that points off the API host.

        A pagination target on another origin would receive our Authorization
        header as a fresh request, so we refuse anything but the API host.
        """
        host = urlsplit(url).netloc
        if host and host != self._base_host:
            raise GitHubApiError(
                0, "pagination link points to an unexpected host", _redact_url(url)
            )
        return url

    def get_job_log(self, repo: str, job_id: int) -> str:
        """Fetch the plain-text log of one job."""
        return self._get(f"/repos/{repo}/actions/jobs/{job_id}/logs").text

    def get_run_logs_zip(self, repo: str, run_id: int) -> bytes:
        """Fetch the zip of all logs for the latest attempt of a run."""
        return self._get(f"/repos/{repo}/actions/runs/{run_id}/logs").content

    def get_attempt_logs_zip(self, repo: str, run_id: int, attempt: int) -> bytes:
        """Fetch the zip of all logs for one attempt of a run."""
        return self._get(f"/repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/logs").content

    def get_pulls_for_commit(self, repo: str, sha: str) -> list[JsonDict]:
        """Fetch the pull requests associated with a commit."""
        data: list[JsonDict] = self._get(f"/repos/{repo}/commits/{sha}/pulls").json()
        return data

    def get_pr_diff(self, repo: str, number: int) -> str:
        """Fetch a pull request as a unified diff."""
        return self._get(f"/repos/{repo}/pulls/{number}", accept=ACCEPT_DIFF).text

    def get_file_content(self, repo: str, path: str, ref: str) -> str | None:
        """Fetch a file's raw content at a ref, or None when it does not exist."""
        response = self._request(
            f"/repos/{repo}/contents/{path}", accept=ACCEPT_RAW, params={"ref": ref}
        )
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        _ensure_success(response)
        return response.text

    def _get(
        self, url: str, *, accept: str | None = None, params: dict[str, str] | None = None
    ) -> httpx.Response:
        response = self._request(url, accept=accept, params=params)
        _ensure_success(response)
        return response

    def _request(
        self, url: str, *, accept: str | None = None, params: dict[str, str] | None = None
    ) -> httpx.Response:
        headers = {"Accept": accept} if accept else None
        retries = 0
        while True:
            response = self._http.get(url, headers=headers, params=params)
            delay = _retry_delay(response)
            if delay is None or retries >= MAX_RETRIES:
                return response
            retries += 1
            time.sleep(delay)


def _retry_delay(response: httpx.Response) -> float | None:
    """Seconds to wait before retrying, or None when the response is final."""
    status = response.status_code
    if status in SERVER_ERROR_STATUSES:
        return SERVER_ERROR_RETRY_SECONDS
    if status not in RATE_LIMIT_STATUSES:
        return None
    retry_after = response.headers.get("Retry-After")
    if retry_after is None:
        return None
    try:
        seconds = float(retry_after)
    except ValueError:
        return None
    return min(max(seconds, 0.0), MAX_RETRY_SLEEP_SECONDS)


def _ensure_success(response: httpx.Response) -> None:
    if not response.is_success:
        raise GitHubApiError(
            response.status_code, _error_message(response), _redact_url(str(response.url))
        )


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("message"), str):
        return str(payload["message"])
    return response.text[:ERROR_PREVIEW_CHARS]
