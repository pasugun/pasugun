import pytest

from engine.toss.rate_limit import DEFAULT_LIMITS, RateLimiter, RateLimitGroup, TokenBucket
from tests.toss.helpers import FakeClock


def test_default_limits_follow_overview_table() -> None:
    assert DEFAULT_LIMITS[RateLimitGroup.MARKET_DATA_CHART] == 20
    assert DEFAULT_LIMITS[RateLimitGroup.MARKET_DATA] == 15
    assert DEFAULT_LIMITS[RateLimitGroup.STOCK_TRADING_TREND] == 10
    assert DEFAULT_LIMITS[RateLimitGroup.STOCK] == 5
    assert DEFAULT_LIMITS[RateLimitGroup.STOCK_ALL] == 1
    assert DEFAULT_LIMITS[RateLimitGroup.ACCOUNT] == 1
    assert DEFAULT_LIMITS[RateLimitGroup.ASSET] == 5


async def test_bucket_allows_burst_then_paces_at_rate() -> None:
    clock = FakeClock()
    bucket = TokenBucket(5, clock=clock.monotonic, sleep=clock.sleep)

    for _ in range(5):
        await bucket.acquire()
    assert clock.sleeps == []

    await bucket.acquire()  # 6번째는 토큰 1개가 찰 때까지(1/5초) 기다린다
    assert clock.sleeps == [pytest.approx(0.2)]


async def test_bucket_never_exceeds_rate_over_one_second() -> None:
    clock = FakeClock()
    bucket = TokenBucket(3, clock=clock.monotonic, sleep=clock.sleep)
    times = []
    for _ in range(9):
        await bucket.acquire()
        times.append(clock.now)

    # 어느 1초 구간에도 (burst 3 + 1초 재충전 3) = 6회를 넘지 않는다
    for t in times:
        assert sum(1 for x in times if t <= x < t + 1) <= 6
    assert times[-1] == pytest.approx(2.0)  # 3개 즉시 + 6개를 1/3초 간격


async def test_observe_respects_server_remaining_and_limit() -> None:
    clock = FakeClock()
    limiter = RateLimiter({RateLimitGroup.STOCK: 5}, clock=clock.monotonic, sleep=clock.sleep)

    limiter.observe(RateLimitGroup.STOCK, {"X-RateLimit-Limit": "2", "X-RateLimit-Remaining": "0"})
    bucket = limiter.bucket(RateLimitGroup.STOCK)
    assert bucket.capacity == 2
    assert bucket.tokens == 0

    await limiter.acquire(RateLimitGroup.STOCK)
    assert clock.sleeps == [pytest.approx(0.5)]


def test_observe_ignores_missing_or_malformed_headers() -> None:
    clock = FakeClock()
    limiter = RateLimiter({RateLimitGroup.STOCK: 5}, clock=clock.monotonic, sleep=clock.sleep)
    limiter.observe(RateLimitGroup.STOCK, {"X-RateLimit-Limit": "abc"})
    assert limiter.bucket(RateLimitGroup.STOCK).capacity == 5
