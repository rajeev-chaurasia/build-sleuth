"""Free-tier budget enforcement: an in-process rpm bucket plus a daily counter on disk.

The daily half has to survive process exit, because a free tier is scored per
calendar day and an eval run is one process among many. State is a small JSON
file per provider per day, so a stale day's file is inert rather than wrong.
"""

import json
import os
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ValidationError

from buildsleuth.llm.registry import ModelSpec, Provider, ProviderQuirks
from buildsleuth.llm.types import RateLimitExceededError, Usage

DEFAULT_STATE_DIR = Path.home() / ".buildsleuth" / "usage"
STATE_FILE_SUFFIX = ".json"
STATE_DATE_FORMAT = "%Y-%m-%d"
TEMP_FILE_SUFFIX = ".tmp"
SECONDS_PER_MINUTE = 60.0
ONE_REQUEST = 1.0
JSON_INDENT = 2
ENCODING = "utf-8"


class DailyUsage(BaseModel):
    """One provider's spend for one calendar day, as stored on disk."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class RateLimiter:
    """Guards one provider's free-tier budget.

    Constructing several of these against the same state directory is safe:
    the daily counters live in the file, not in the instance, so only the
    per-minute burst allowance is per-instance.
    """

    def __init__(
        self,
        provider: Provider,
        quirks: ProviderQuirks,
        *,
        state_dir: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._provider = provider
        self._quirks = quirks
        self._state_dir = state_dir if state_dir is not None else DEFAULT_STATE_DIR
        self._clock = clock
        self._sleep = sleep
        self._today = today
        self._capacity = float(quirks.rpm) if quirks.rpm else 0.0
        self._refill_per_second = self._capacity / SECONDS_PER_MINUTE
        self._tokens = self._capacity
        self._last_refill = clock()

    @classmethod
    def for_spec(
        cls,
        spec: ModelSpec,
        *,
        state_dir: Path | None = None,
    ) -> "RateLimiter":
        """Build a limiter for a registry entry using real time."""
        return cls(spec.provider, spec.quirks, state_dir=state_dir)

    @property
    def state_path(self) -> Path:
        """Where today's counters for this provider live."""
        stamp = self._today().strftime(STATE_DATE_FORMAT)
        return self._state_dir / f"{self._provider.value}-{stamp}{STATE_FILE_SUFFIX}"

    def usage_today(self) -> DailyUsage:
        """Read today's counters, treating a missing or unreadable file as zero."""
        try:
            raw = self.state_path.read_text(encoding=ENCODING)
        except OSError:
            return DailyUsage()
        try:
            return DailyUsage.model_validate_json(raw)
        except (ValidationError, ValueError):
            # A truncated write from a killed process should cost us the day's
            # history, not the run.
            return DailyUsage()

    def remaining_requests_today(self) -> int | None:
        """Requests left in today's budget, or None when the provider caps none."""
        if self._quirks.rpd is None:
            return None
        return max(0, self._quirks.rpd - self.usage_today().requests)

    def preflight(self, n_requests: int) -> None:
        """Refuse a planned batch that cannot finish inside today's budget.

        An 80-case eval that dies at case 40 wastes the quota and produces no
        scorecard, so the whole batch is checked before the first call.
        """
        usage = self.usage_today()
        if self._quirks.tpd is not None and usage.total_tokens >= self._quirks.tpd:
            raise RateLimitExceededError(
                f"{self._provider.value} daily token budget is spent: "
                f"{usage.total_tokens} of {self._quirks.tpd} tokens used today. "
                "Wait for the provider's daily reset or pick another model."
            )
        if self._quirks.rpd is None:
            return
        remaining = max(0, self._quirks.rpd - usage.requests)
        if n_requests > remaining:
            raise RateLimitExceededError(
                f"{self._provider.value} daily request budget cannot cover this run: "
                f"{n_requests} requests planned but {remaining} of {self._quirks.rpd} remain "
                f"(counted in {self.state_path}). "
                "Wait for the provider's daily reset, pick another model, or use a smaller subset."
            )

    def acquire(self) -> None:
        """Block until one request fits the per-minute bucket, or raise if the day is spent."""
        self.preflight(1)
        if self._refill_per_second <= 0.0:
            return
        self._refill()
        if self._tokens < ONE_REQUEST:
            self._sleep((ONE_REQUEST - self._tokens) / self._refill_per_second)
            self._refill()
            # We already waited out the deficit, so grant the request even if
            # the clock has not visibly moved.
            self._tokens = max(self._tokens, ONE_REQUEST)
        self._tokens -= ONE_REQUEST

    def record(self, usage: Usage) -> None:
        """Add one request and its tokens to today's persistent counters."""
        path = self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Read-modify-write per call, so a second process picks up whatever the
        # first has already spent. Last writer wins on a true collision, which
        # is accurate enough for a budget guard.
        current = self.usage_today()
        updated = DailyUsage(
            requests=current.requests + 1,
            input_tokens=current.input_tokens + usage.input_tokens,
            output_tokens=current.output_tokens + usage.output_tokens,
        )
        self._write(path, updated)

    def _write(self, path: Path, usage: DailyUsage) -> None:
        temp = path.with_suffix(TEMP_FILE_SUFFIX)
        temp.write_text(
            json.dumps(usage.model_dump(), indent=JSON_INDENT),
            encoding=ENCODING,
        )
        os.replace(temp, path)

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last_refill)
        self._last_refill = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_per_second)
