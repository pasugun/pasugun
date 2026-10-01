"""투자자별 매매동향 동기화 (GET /api/v1/stocks/{symbol}/investor-trading, 국내 전용).

당일 기록은 잠정치(개인 null)라서 최근 N거래일은 매번 다시 받아 확정치로 덮어쓴다.
"""

import asyncio
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from engine.collect.db import upsert
from engine.collect.runner import Progress, SyncResult, run_per_symbol
from engine.db.models import InvestorTradingDaily
from engine.toss.api import TossApi
from engine.toss.models import StockInvestorTradingRecord
from engine.toss.pagination import iter_investor_trading

SOURCE = "toss"
UPDATE_COLUMNS = ("foreigner_net", "institution_net", "individual_net", "source")


def investor_row(symbol: str, record: StockInvestorTradingRecord) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "date": record.date,
        "foreigner_net": int(record.foreigner.netBuyVolume),
        "institution_net": int(record.institution.netBuyVolume),
        "individual_net": int(record.individual.netBuyVolume) if record.individual else None,
        "source": SOURCE,
    }


async def plan_fetch_since(
    session: AsyncSession, symbol: str, since: date, recent_days: int, *, extend_history: bool
) -> date:
    """candles.plan_fetch_since 와 같은 규칙."""
    cond = (InvestorTradingDaily.symbol == symbol, InvestorTradingDaily.source == SOURCE)
    earliest = await session.scalar(select(func.min(InvestorTradingDaily.date)).where(*cond))
    if earliest is None or (extend_history and since < earliest):
        return since
    recent = (
        await session.scalars(
            select(InvestorTradingDaily.date)
            .where(*cond)
            .order_by(InvestorTradingDaily.date.desc())
            .limit(recent_days)
        )
    ).all()
    return min(recent)


async def sync_investor_trading(
    api: TossApi,
    session: AsyncSession,
    symbols: list[str],
    *,
    since: date,
    refetch_recent_days: int,
    extend_history: bool = False,
    concurrency: int = 4,
    progress: Progress | None = None,
) -> SyncResult:
    db_lock = asyncio.Lock()

    async def work(symbol: str, result: SyncResult) -> int:
        async with db_lock:
            fetch_since = await plan_fetch_since(
                session, symbol, since, refetch_recent_days, extend_history=extend_history
            )
        rows = [
            investor_row(symbol, r)
            async for r in iter_investor_trading(api, symbol, since=fetch_since)
        ]
        async with db_lock:
            await upsert(
                session, InvestorTradingDaily, rows, keys=["symbol", "date"], update=UPDATE_COLUMNS
            )
            await session.commit()
        return len(rows)

    return await run_per_symbol(
        "investor_trading", symbols, work, concurrency=concurrency, progress=progress
    )
