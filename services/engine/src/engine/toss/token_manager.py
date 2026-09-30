"""토스 액세스 토큰 발급·갱신을 한 곳에서 관리한다.

토큰은 클라이언트당 1개만 유효해서, 새로 발급하면 다른 프로세스가 쓰던 토큰이 즉시 무효화된다.
그래서 발급은 항상 저장소의 배타 구간(DB advisory lock) 안에서 하고, 들어간 뒤 저장소를 다시 읽어
다른 프로세스가 이미 새 토큰을 받아 뒀다면 그걸 쓴다.

토큰 값은 절대 로그·예외 메시지에 넣지 않는다.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from engine.db.models import ApiToken
from engine.toss.errors import TossAuthError
from engine.toss.models import OAuth2TokenResponse
from engine.toss.rate_limit import RateLimiter, RateLimitGroup

logger = logging.getLogger(__name__)

PROVIDER = "toss"
TOKEN_PATH = "/oauth2/token"
# 만료 직전 토큰으로 요청하다 401 을 받지 않도록 여유를 두고 갱신한다.
REFRESH_MARGIN = timedelta(minutes=5)


@dataclass(frozen=True)
class StoredToken:
    access_token: str
    expires_at: datetime

    def __repr__(self) -> str:
        return f"StoredToken(access_token='***', expires_at={self.expires_at!r})"

    __str__ = __repr__


def mask_token(token: str) -> str:
    """로그용 표기. 토큰 문자는 한 글자도 남기지 않는다."""
    return f"***(len={len(token)})"


# --- 저장소 -------------------------------------------------------------------


class TokenTxn(Protocol):
    async def load(self) -> StoredToken | None: ...
    async def save(self, token: StoredToken) -> None: ...


class TokenStore(Protocol):
    async def load(self) -> StoredToken | None: ...

    def exclusive(self) -> AbstractAsyncContextManager[TokenTxn]:
        """프로세스 간 배타 구간. 이 안에서만 토큰을 발급·저장한다."""
        ...


class _MemoryTxn:
    def __init__(self, store: "InMemoryTokenStore") -> None:
        self._store = store

    async def load(self) -> StoredToken | None:
        return self._store.token

    async def save(self, token: StoredToken) -> None:
        self._store.token = token


class InMemoryTokenStore:
    """테스트·mock 모드용.

    같은 인스턴스를 공유하면 여러 프로세스가 DB 를 공유하는 상황을 흉내 낸다.
    """

    def __init__(self, token: StoredToken | None = None) -> None:
        self.token = token
        self._lock = asyncio.Lock()

    async def load(self) -> StoredToken | None:
        return self.token

    @asynccontextmanager
    async def exclusive(self) -> AsyncIterator[TokenTxn]:
        async with self._lock:
            yield _MemoryTxn(self)


class _DbTxn:
    def __init__(self, conn: AsyncConnection, provider: str) -> None:
        self._conn = conn
        self._provider = provider

    async def load(self) -> StoredToken | None:
        return await _load_token(self._conn, self._provider)

    async def save(self, token: StoredToken) -> None:
        stmt = insert(ApiToken).values(
            provider=self._provider,
            access_token=token.access_token,
            expires_at=token.expires_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[ApiToken.provider],
            set_={
                "access_token": stmt.excluded.access_token,
                "expires_at": stmt.excluded.expires_at,
                "updated_at": text("now()"),
            },
        )
        await self._conn.execute(stmt)


async def _load_token(conn: AsyncConnection, provider: str) -> StoredToken | None:
    row = (
        await conn.execute(
            select(ApiToken.access_token, ApiToken.expires_at).where(ApiToken.provider == provider)
        )
    ).first()
    return StoredToken(row.access_token, row.expires_at) if row else None


class DbTokenStore:
    """api_tokens 테이블. 배타 구간은 트랜잭션 단위 advisory lock 으로 잡는다(행이 없어도 동작)."""

    def __init__(self, engine: AsyncEngine, provider: str = PROVIDER) -> None:
        self._engine = engine
        self._provider = provider

    async def load(self) -> StoredToken | None:
        async with self._engine.connect() as conn:
            return await _load_token(conn, self._provider)

    @asynccontextmanager
    async def exclusive(self) -> AsyncIterator[TokenTxn]:
        async with self._engine.begin() as conn:
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"api_tokens:{self._provider}"},
            )
            yield _DbTxn(conn, self._provider)


# --- 발급기 -------------------------------------------------------------------


TokenIssuer = Callable[[], Awaitable[OAuth2TokenResponse]]


def http_token_issuer(
    http: httpx.AsyncClient,
    client_id: str,
    client_secret: str,
    rate_limiter: RateLimiter | None = None,
) -> TokenIssuer:
    async def issue() -> OAuth2TokenResponse:
        if rate_limiter is not None:
            await rate_limiter.acquire(RateLimitGroup.AUTH)
        response = await http.post(
            TOKEN_PATH,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        if response.status_code != 200:
            raise TossAuthError.from_response(response)
        return OAuth2TokenResponse.model_validate(response.json())

    return issue


# --- TokenManager -------------------------------------------------------------


class TokenManager:
    def __init__(
        self,
        store: TokenStore,
        issuer: TokenIssuer,
        *,
        now: Callable[[], datetime],
        refresh_margin: timedelta = REFRESH_MARGIN,
    ) -> None:
        self._store = store
        self._issuer = issuer
        self._now = now
        self._margin = refresh_margin
        self._cached: StoredToken | None = None
        self._local_lock = asyncio.Lock()  # 같은 프로세스 안의 동시 갱신을 한 번으로 모은다

    def _is_fresh(self, token: StoredToken | None) -> bool:
        return token is not None and token.expires_at - self._margin > self._now()

    async def get_token(self) -> str:
        if self._is_fresh(self._cached):
            return self._cached.access_token  # type: ignore[union-attr]
        stored = await self._store.load()
        if self._is_fresh(stored):
            self._cached = stored
            return stored.access_token  # type: ignore[union-attr]
        return await self.refresh(stale_token=stored.access_token if stored else None)

    async def refresh(self, stale_token: str | None) -> str:
        """stale_token 을 대체할 새 토큰을 얻는다.

        배타 구간 안에서 저장소를 다시 읽어, 이미 다른 프로세스·코루틴이
        stale_token 이 아닌 새 토큰을 받아 뒀다면 발급하지 않고 그걸 쓴다.
        """
        async with self._local_lock:
            async with self._store.exclusive() as txn:
                current = await txn.load()
                if (
                    current is not None
                    and current.access_token != stale_token
                    and self._is_fresh(current)
                ):
                    self._cached = current
                    return current.access_token

                issued = await self._issuer()
                token = StoredToken(
                    access_token=issued.access_token,
                    expires_at=self._now() + timedelta(seconds=issued.expires_in),
                )
                await txn.save(token)
                self._cached = token
                logger.info(
                    "토스 액세스 토큰을 새로 발급했습니다 (token=%s, expires_at=%s)",
                    mask_token(token.access_token),
                    token.expires_at.isoformat(),
                )
                return token.access_token
