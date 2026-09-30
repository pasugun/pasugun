"""Rate Limits Group 별 토큰 버킷.

한도는 docs/toss/overview.md 의 표를 따른다. 서버가 응답 헤더(X-RateLimit-Limit / Remaining)로
알려주는 값이 있으면 그 값을 우선한다.

한 프로세스 안에서만 조율한다. 여러 프로세스가 같은 그룹을 동시에 쓰면 합계가 한도를 넘을 수 있고,
그때는 429 + Retry-After 재시도가 안전망이 된다.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from enum import StrEnum


class RateLimitGroup(StrEnum):
    AUTH = "AUTH"
    ACCOUNT = "ACCOUNT"
    ASSET = "ASSET"
    STOCK = "STOCK"
    STOCK_ALL = "STOCK_ALL"
    STOCK_TRADING_TREND = "STOCK_TRADING_TREND"
    MARKET_INFO = "MARKET_INFO"
    MARKET_DATA = "MARKET_DATA"
    MARKET_DATA_CHART = "MARKET_DATA_CHART"
    RANKING = "RANKING"
    MARKET_INDICATOR_PRICE = "MARKET_INDICATOR_PRICE"
    MARKET_INDICATOR = "MARKET_INDICATOR"
    MARKET_INDICATOR_CHART = "MARKET_INDICATOR_CHART"
    ORDER_HISTORY = "ORDER_HISTORY"
    ORDER_INFO = "ORDER_INFO"


# 초당 최대 요청 수 (overview.md "Rate Limits" 표)
DEFAULT_LIMITS: dict[RateLimitGroup, int] = {
    RateLimitGroup.AUTH: 5,
    RateLimitGroup.ACCOUNT: 1,
    RateLimitGroup.ASSET: 5,
    RateLimitGroup.STOCK: 5,
    RateLimitGroup.STOCK_ALL: 1,
    RateLimitGroup.STOCK_TRADING_TREND: 10,
    RateLimitGroup.MARKET_INFO: 3,
    RateLimitGroup.MARKET_DATA: 15,
    RateLimitGroup.MARKET_DATA_CHART: 20,
    RateLimitGroup.RANKING: 5,
    RateLimitGroup.MARKET_INDICATOR_PRICE: 10,
    RateLimitGroup.MARKET_INDICATOR: 10,
    RateLimitGroup.MARKET_INDICATOR_CHART: 5,
    RateLimitGroup.ORDER_HISTORY: 5,
    # 09:00~09:10 KST 피크시간에는 3회. 보수적으로 항상 3회를 쓴다.
    RateLimitGroup.ORDER_INFO: 3,
}

# 부동소수 오차로 토큰이 0.9999999 처럼 남아 아주 짧은 sleep 이 반복되는 것을 막는다.
_EPSILON = 1e-9

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class TokenBucket:
    """용량 = 초당 한도(burst), 초당 한도만큼 재충전."""

    def __init__(self, rate_per_sec: int, *, clock: Clock, sleep: Sleep) -> None:
        self.capacity = float(rate_per_sec)
        self.rate = float(rate_per_sec)
        self.tokens = float(rate_per_sec)
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self.tokens = min(self.capacity, self.tokens + (now - self._updated) * self.rate)
        self._updated = now

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                self._refill()
                if self.tokens >= 1 - _EPSILON:
                    self.tokens = max(0.0, self.tokens - 1)
                    return
                await self._sleep((1 - self.tokens) / self.rate)

    def observe(self, limit: int | None, remaining: int | None) -> None:
        """서버가 알려준 한도·잔량을 반영한다. 서버 값이 더 보수적이면 그쪽을 따른다."""
        self._refill()
        if limit is not None and limit > 0 and limit != self.capacity:
            self.capacity = float(limit)
            self.rate = float(limit)
            self.tokens = min(self.tokens, self.capacity)
        if remaining is not None and remaining >= 0:
            self.tokens = min(self.tokens, float(remaining))


def _int_header(headers: Mapping[str, str], name: str) -> int | None:
    value = headers.get(name)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


class RateLimiter:
    def __init__(
        self,
        limits: Mapping[RateLimitGroup, int] | None = None,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._buckets = {
            group: TokenBucket(rate, clock=clock, sleep=sleep)
            for group, rate in (limits or DEFAULT_LIMITS).items()
        }

    def bucket(self, group: RateLimitGroup) -> TokenBucket:
        return self._buckets[group]

    async def acquire(self, group: RateLimitGroup) -> None:
        await self._buckets[group].acquire()

    def observe(self, group: RateLimitGroup, headers: Mapping[str, str]) -> None:
        self._buckets[group].observe(
            limit=_int_header(headers, "X-RateLimit-Limit"),
            remaining=_int_header(headers, "X-RateLimit-Remaining"),
        )
