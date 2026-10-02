from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import sessionmaker

from engine.config import get_settings


def _connect_args() -> dict[str, str]:
    # 세션 타임존을 고정해 timestamptz 값이 항상 KST로 읽히게 한다.
    return {"options": f"-c timezone={get_settings().timezone}"}


def make_engine(database_url: str | None = None, **kwargs: Any) -> Engine:
    return create_engine(
        database_url or get_settings().database_url,
        pool_pre_ping=True,
        connect_args=_connect_args(),
        **kwargs,
    )


def make_async_engine(database_url: str | None = None, **kwargs: Any) -> AsyncEngine:
    """psycopg3 는 같은 postgresql+psycopg:// URL 로 async 도 지원한다."""
    return create_async_engine(
        database_url or get_settings().database_url,
        pool_pre_ping=True,
        connect_args=_connect_args(),
        **kwargs,
    )


def make_async_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)
