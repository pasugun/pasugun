import os
import socket
from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from engine.config import ENGINE_ROOT, get_settings
from engine.db.models import Base
from engine.db.session import make_async_engine, make_async_sessionmaker

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
_real_connect = socket.socket.connect

# 시드 데이터라서 테스트 사이에 비우지 않는 테이블
_KEEP_TABLES = {"robots", "robot_settings"}


def _guarded_connect(self: socket.socket, address: object) -> None:
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, str) and host not in _LOCAL_HOSTS and self.family != socket.AF_UNIX:
        raise RuntimeError(f"테스트에서 외부 네트워크 연결을 시도했습니다: {address!r}")
    _real_connect(self, address)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _block_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """테스트가 실제 토스 API 등 외부로 나가지 못하게 막는다(로컬 DB 는 허용)."""
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[str]:
    """개발 DB 옆에 <이름>_test DB 를 만들고 마이그레이션을 올린다. DB 서버가 없으면 건너뛴다.

    이 fixture 를 쓰는 동안 DATABASE_URL 은 테스트 DB 를 가리킨다(개발 DB 를 건드리지 않는다).
    """
    base = make_url(get_settings().database_url)
    test_url = base.set(database=f"{base.database}_test")
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": test_url.database}
            )
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{test_url.database}"'))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"테스트 DB 를 쓸 수 없어 건너뜁니다: {type(exc).__name__}")
    finally:
        admin.dispose()

    url = test_url.render_as_string(hide_password=False)
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    command.upgrade(Config(str(ENGINE_ROOT / "alembic.ini")), "head")
    yield url
    if previous is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous
    get_settings.cache_clear()


@pytest.fixture
async def sessions(test_database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """비워 둔 테스트 DB 의 세션 팩토리."""
    engine = make_async_engine(test_database_url)
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables if t.name not in _KEEP_TABLES)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield make_async_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture
async def session(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sessions() as s:
        yield s
