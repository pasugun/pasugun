from datetime import datetime, timedelta

import httpx

from engine.clock import KST
from engine.toss.client import TossClient
from engine.toss.models import OAuth2TokenResponse
from engine.toss.rate_limit import RateLimiter
from engine.toss.token_manager import InMemoryTokenStore, StoredToken, TokenManager

BASE_URL = "https://openapi.tossinvest.com"
NOW = datetime(2026, 9, 30, 10, 0, tzinfo=KST)


class FakeClock:
    """sleep 하면 시간이 그만큼 흐르는 가짜 시계. 실제로 기다리지 않는다."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeIssuer:
    """호출될 때마다 tok-1, tok-2 ... 를 발급한다."""

    def __init__(self, expires_in: int = 86400) -> None:
        self.calls = 0
        self.expires_in = expires_in

    async def __call__(self) -> OAuth2TokenResponse:
        self.calls += 1
        return OAuth2TokenResponse(
            access_token=f"tok-{self.calls}", token_type="Bearer", expires_in=self.expires_in
        )


def valid_token(value: str = "tok-0") -> StoredToken:
    return StoredToken(access_token=value, expires_at=NOW + timedelta(hours=12))


def make_client(
    *,
    store: InMemoryTokenStore | None = None,
    issuer: FakeIssuer | None = None,
    clock: FakeClock | None = None,
    account_seq: str | None = "1",
) -> tuple[TossClient, FakeIssuer, FakeClock]:
    clock = clock or FakeClock()
    issuer = issuer or FakeIssuer()
    store = store if store is not None else InMemoryTokenStore(valid_token())
    manager = TokenManager(store, issuer, now=lambda: NOW)
    limiter = RateLimiter(clock=clock.monotonic, sleep=clock.sleep)
    client = TossClient(
        httpx.AsyncClient(base_url=BASE_URL),
        manager,
        limiter,
        account_seq=account_seq,
        sleep=clock.sleep,
        jitter=lambda: 0.0,
    )
    return client, issuer, clock


def envelope(result: object) -> dict[str, object]:
    return {"result": result}


def error_body(code: str, message: str = "에러", request_id: str = "req-123") -> dict[str, object]:
    return {"error": {"requestId": request_id, "code": code, "message": message}}
