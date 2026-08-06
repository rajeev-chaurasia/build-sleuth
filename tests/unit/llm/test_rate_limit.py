import json
from datetime import date
from pathlib import Path

import pytest

from buildsleuth.llm.rate_limit import RateLimiter
from buildsleuth.llm.registry import Provider, ProviderQuirks
from buildsleuth.llm.types import RateLimitExceededError, Usage

DAY = date(2026, 8, 5)
NEXT_DAY = date(2026, 8, 6)


class FakeClock:
    """Monotonic clock whose only way to advance is a sleep we can inspect."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


def quirks(
    *,
    rpm: int | None = None,
    rpd: int | None = None,
    tpd: int | None = None,
) -> ProviderQuirks:
    return ProviderQuirks(
        native_json_schema=True,
        supports_tools=True,
        rpm=rpm,
        rpd=rpd,
        tpd=tpd,
        context_window=1024,
        max_retries=1,
    )


def limiter(
    tmp_path: Path,
    *,
    rpm: int | None = None,
    rpd: int | None = None,
    tpd: int | None = None,
    clock: FakeClock | None = None,
    today: date = DAY,
) -> RateLimiter:
    fake = clock if clock is not None else FakeClock()
    return RateLimiter(
        Provider.GROQ,
        quirks(rpm=rpm, rpd=rpd, tpd=tpd),
        state_dir=tmp_path,
        clock=fake.time,
        sleep=fake.sleep,
        today=lambda: today,
    )


def test_daily_counter_persists_across_instances(tmp_path: Path) -> None:
    first = limiter(tmp_path, rpd=10)
    first.record(Usage(input_tokens=100, output_tokens=20))
    first.record(Usage(input_tokens=50, output_tokens=5))

    second = limiter(tmp_path, rpd=10)
    usage = second.usage_today()
    assert usage.requests == 2
    assert usage.input_tokens == 150
    assert usage.output_tokens == 25
    assert second.remaining_requests_today() == 8


def test_state_file_uses_the_documented_keys(tmp_path: Path) -> None:
    rate_limiter = limiter(tmp_path, rpd=10)
    rate_limiter.record(Usage(input_tokens=7, output_tokens=3))
    stored = json.loads(rate_limiter.state_path.read_text(encoding="utf-8"))
    assert stored == {"requests": 1, "input_tokens": 7, "output_tokens": 3}


def test_remaining_requests_is_none_without_a_daily_cap(tmp_path: Path) -> None:
    assert limiter(tmp_path).remaining_requests_today() is None


def test_acquire_raises_once_the_daily_requests_are_spent(tmp_path: Path) -> None:
    rate_limiter = limiter(tmp_path, rpd=2)
    rate_limiter.acquire()
    rate_limiter.record(Usage())
    rate_limiter.acquire()
    rate_limiter.record(Usage())
    with pytest.raises(RateLimitExceededError) as excinfo:
        rate_limiter.acquire()
    assert "daily request budget" in str(excinfo.value)


def test_acquire_raises_once_the_daily_tokens_are_spent(tmp_path: Path) -> None:
    rate_limiter = limiter(tmp_path, tpd=100)
    rate_limiter.record(Usage(input_tokens=90, output_tokens=10))
    with pytest.raises(RateLimitExceededError) as excinfo:
        rate_limiter.acquire()
    assert "daily token budget" in str(excinfo.value)


def test_preflight_permits_a_run_that_fits(tmp_path: Path) -> None:
    rate_limiter = limiter(tmp_path, rpd=100)
    for _ in range(20):
        rate_limiter.record(Usage())
    rate_limiter.preflight(80)


def test_preflight_refuses_an_oversized_run(tmp_path: Path) -> None:
    rate_limiter = limiter(tmp_path, rpd=100)
    for _ in range(21):
        rate_limiter.record(Usage())
    with pytest.raises(RateLimitExceededError) as excinfo:
        rate_limiter.preflight(80)
    message = str(excinfo.value)
    assert "80 requests planned" in message
    assert "79 of 100 remain" in message


def test_preflight_is_a_no_op_without_a_daily_cap(tmp_path: Path) -> None:
    limiter(tmp_path).preflight(10_000)


def test_corrupt_state_file_resets_instead_of_crashing(tmp_path: Path) -> None:
    rate_limiter = limiter(tmp_path, rpd=10)
    rate_limiter.state_path.parent.mkdir(parents=True, exist_ok=True)
    rate_limiter.state_path.write_text('{"requests": 3, "input_', encoding="utf-8")

    assert rate_limiter.usage_today().requests == 0
    rate_limiter.record(Usage(input_tokens=1, output_tokens=1))
    assert rate_limiter.usage_today().requests == 1


def test_state_file_with_wrong_types_resets(tmp_path: Path) -> None:
    rate_limiter = limiter(tmp_path, rpd=10)
    rate_limiter.state_path.parent.mkdir(parents=True, exist_ok=True)
    rate_limiter.state_path.write_text('{"requests": "many"}', encoding="utf-8")
    assert rate_limiter.usage_today().requests == 0


def test_day_rollover_starts_a_fresh_counter(tmp_path: Path) -> None:
    yesterday = limiter(tmp_path, rpd=5, today=DAY)
    for _ in range(5):
        yesterday.record(Usage(input_tokens=10, output_tokens=10))
    assert yesterday.remaining_requests_today() == 0

    today = limiter(tmp_path, rpd=5, today=NEXT_DAY)
    assert today.usage_today().requests == 0
    assert today.remaining_requests_today() == 5
    assert today.state_path != yesterday.state_path


def test_token_bucket_bursts_up_to_rpm_then_waits(tmp_path: Path) -> None:
    clock = FakeClock()
    rate_limiter = limiter(tmp_path, rpm=2, clock=clock)

    rate_limiter.acquire()
    rate_limiter.acquire()
    assert clock.sleeps == []

    # At 2 requests per minute one token takes 30 seconds to come back.
    rate_limiter.acquire()
    assert clock.sleeps == [pytest.approx(30.0)]


def test_token_bucket_credits_elapsed_time(tmp_path: Path) -> None:
    clock = FakeClock()
    rate_limiter = limiter(tmp_path, rpm=2, clock=clock)
    rate_limiter.acquire()
    rate_limiter.acquire()

    clock.advance(15.0)
    rate_limiter.acquire()
    assert clock.sleeps == [pytest.approx(15.0)]


def test_no_rpm_means_no_waiting(tmp_path: Path) -> None:
    clock = FakeClock()
    rate_limiter = limiter(tmp_path, clock=clock)
    for _ in range(50):
        rate_limiter.acquire()
    assert clock.sleeps == []
