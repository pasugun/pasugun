"""일봉 동기화 (GET /api/v1/candles, 수정주가).

- 처음 받는 종목: since 부터 전부
- 이미 있는 종목: 저장된 최근 N거래일부터 다시 받아 덮어쓴다(수정주가·통합시세 거래량 반영).
- extend_history=True(백필)이고 since 가 저장된 가장 이른 날짜보다 과거면 since 부터 받는다.
  매일 배치는 False 라서, API 가 가진 기간이 since 보다 짧은 종목(신규 상장 등)을
  매번 전부 다시 받지 않는다.
- 다시 받은 구간의 종가가 저장값과 다르면 액면분할 등으로 과거 수정주가 전체가 바뀐 것이므로
  저장된 전체 기간을 다시 받는다.
"""

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from engine.clock import KST
from engine.collect.db import upsert
from engine.collect.runner import Progress, SyncResult, run_per_symbol
from engine.db.models import CandleDaily
from engine.toss.api import TossApi
from engine.toss.models import Candle
from engine.toss.pagination import iter_candles

SOURCE = "toss"
UPDATE_COLUMNS = ("open", "high", "low", "close", "volume", "adjusted", "source")


@dataclass(frozen=True)
class StoredCandles:
    earliest: date | None
    recent_closes: dict[date, Decimal]  # 최근 N거래일

    @property
    def refetch_from(self) -> date | None:
        return min(self.recent_closes) if self.recent_closes else None


def candle_row(symbol: str, candle: Candle) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "date": candle.timestamp.astimezone(KST).date(),
        "open": candle.openPrice,
        "high": candle.highPrice,
        "low": candle.lowPrice,
        "close": candle.closePrice,
        "volume": int(candle.volume),
        "adjusted": True,
        "source": SOURCE,
    }


async def load_stored(session: AsyncSession, symbol: str, recent_days: int) -> StoredCandles:
    base = select(CandleDaily).where(CandleDaily.symbol == symbol, CandleDaily.source == SOURCE)
    earliest = await session.scalar(
        select(func.min(CandleDaily.date)).where(
            CandleDaily.symbol == symbol, CandleDaily.source == SOURCE
        )
    )
    recent = (
        await session.scalars(base.order_by(CandleDaily.date.desc()).limit(recent_days))
    ).all()
    return StoredCandles(earliest=earliest, recent_closes={c.date: c.close for c in recent})


def plan_fetch_since(stored: StoredCandles, since: date, *, extend_history: bool) -> date:
    if stored.earliest is None or (extend_history and since < stored.earliest):
        return since
    return stored.refetch_from or since


async def fetch_candle_rows(api: TossApi, symbol: str, since: date) -> list[dict[str, Any]]:
    start = datetime.combine(since, time.min, KST)
    return [
        candle_row(symbol, c)
        async for c in iter_candles(api, symbol, "1d", since=start, adjusted=True)
    ]


def adjustment_changed(stored: StoredCandles, rows: list[dict[str, Any]]) -> bool:
    fetched = {r["date"]: r["close"] for r in rows}
    return any(
        d in fetched and Decimal(fetched[d]) != close for d, close in stored.recent_closes.items()
    )


async def sync_daily_candles(
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
            stored = await load_stored(session, symbol, refetch_recent_days)
        rows = await fetch_candle_rows(
            api, symbol, plan_fetch_since(stored, since, extend_history=extend_history)
        )
        if stored.earliest is not None and adjustment_changed(stored, rows):
            result.note("adjusted_refetch", symbol)
            full_since = min(stored.earliest, since) if extend_history else stored.earliest
            rows = await fetch_candle_rows(api, symbol, full_since)
        async with db_lock:
            await upsert(session, CandleDaily, rows, keys=["symbol", "date"], update=UPDATE_COLUMNS)
            await session.commit()
        return len(rows)

    return await run_per_symbol(
        "candles", symbols, work, concurrency=concurrency, progress=progress
    )
