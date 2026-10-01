"""DbTokenStore 통합 테스트. 테스트 DB(docker compose up db)가 없으면 건너뛴다."""

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine

from engine.db.models import ApiToken
from engine.db.session import make_async_engine
from engine.toss.token_manager import DbTokenStore, StoredToken, TokenManager
from tests.toss.helpers import NOW, FakeIssuer


@pytest.fixture
async def engine(test_database_url: str) -> AsyncEngine:
    engine = make_async_engine(test_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def provider(engine: AsyncEngine) -> str:
    name = f"test-{uuid.uuid4().hex[:8]}"
    yield name
    async with engine.begin() as conn:
        await conn.execute(delete(ApiToken).where(ApiToken.provider == name))


async def test_save_and_load_roundtrip(engine: AsyncEngine, provider: str) -> None:
    store = DbTokenStore(engine, provider)
    token = StoredToken("db-token", NOW + timedelta(hours=1))

    async with store.exclusive() as txn:
        assert await txn.load() is None
        await txn.save(token)

    assert await store.load() == token


async def test_two_processes_refresh_concurrently_issue_once(
    engine: AsyncEngine, provider: str
) -> None:
    """서로 다른 TokenManager(=프로세스)가 같은 DB 로 동시에 갱신해도 발급은 한 번."""
    issuer = FakeIssuer()
    other_engine = make_async_engine(engine.url.render_as_string(hide_password=False))
    try:
        managers = [
            TokenManager(DbTokenStore(e, provider), issuer, now=lambda: NOW)
            for e in (engine, other_engine)
        ]
        tokens = await asyncio.gather(*(m.get_token() for m in managers))
    finally:
        await other_engine.dispose()

    assert tokens == ["tok-1", "tok-1"]
    assert issuer.calls == 1
