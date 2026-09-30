from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker

from engine.config import get_settings


def make_engine(database_url: str | None = None, **kwargs: Any) -> Engine:
    settings = get_settings()
    return create_engine(
        database_url or settings.database_url,
        pool_pre_ping=True,
        # 세션 타임존을 고정해 timestamptz 값이 항상 KST로 읽히게 한다.
        connect_args={"options": f"-c timezone={settings.timezone}"},
        **kwargs,
    )


SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)
