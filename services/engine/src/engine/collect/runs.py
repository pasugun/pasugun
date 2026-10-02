from datetime import date
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from engine.db.models import CollectRun


async def start_run(session: AsyncSession, job: str, trade_date: date) -> int:
    run = CollectRun(job=job, trade_date=trade_date, status="running")
    session.add(run)
    await session.commit()
    return run.id


async def finish_run(
    session: AsyncSession, run_id: int, status: str, detail: dict[str, Any] | None = None
) -> None:
    await session.execute(
        update(CollectRun)
        .where(CollectRun.id == run_id)
        .values(status=status, finished_at=func.now(), detail=detail or {})
    )
    await session.commit()


async def record_skipped(session: AsyncSession, job: str, trade_date: date, reason: str) -> None:
    run_id = await start_run(session, job, trade_date)
    await finish_run(session, run_id, "skipped", {"reason": reason})


async def has_finished_run(session: AsyncSession, job: str, trade_date: date) -> bool:
    """그날 작업이 성공(부분 성공·휴장 스킵 포함)으로 끝난 적이 있는지."""
    found = await session.scalar(
        select(CollectRun.id)
        .where(
            CollectRun.job == job,
            CollectRun.trade_date == trade_date,
            CollectRun.status.in_(("success", "partial", "skipped")),
        )
        .limit(1)
    )
    return found is not None
