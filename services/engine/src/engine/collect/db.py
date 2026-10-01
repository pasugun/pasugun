from collections.abc import Iterable, Sequence
from itertools import islice
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from engine.db.models import Base

CHUNK_SIZE = 1000


def _chunks(rows: Sequence[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    it = iter(rows)
    while chunk := list(islice(it, size)):
        yield chunk


async def upsert(
    session: AsyncSession,
    model: type[Base],
    rows: Sequence[dict[str, Any]],
    *,
    keys: Sequence[str],
    update: Sequence[str] | None = None,
) -> int:
    """PK(keys) 충돌 시 update 컬럼을 덮어쓴다. update 가 비어 있으면 기존 행을 그대로 둔다."""
    if not rows:
        return 0
    for chunk in _chunks(rows, CHUNK_SIZE):
        stmt = insert(model).values(chunk)
        if update:
            set_: dict[str, Any] = {col: stmt.excluded[col] for col in update}
            if "updated_at" in model.__table__.columns:
                set_["updated_at"] = text("now()")
            stmt = stmt.on_conflict_do_update(index_elements=list(keys), set_=set_)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=list(keys))
        await session.execute(stmt)
    return len(rows)
