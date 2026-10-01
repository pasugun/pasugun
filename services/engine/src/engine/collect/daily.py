"""장 마감 배치: 종목 마스터 → 일봉 → 대상 종목 선정 → 수급.

휴장일에는 아무것도 하지 않는다. 각 단계는 종목별 마지막 저장일부터 이어 받으므로
하루 실패해도 다음 실행에서 빠진 날짜가 자동으로 채워진다.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from engine.collect.calendar import is_trading_day
from engine.collect.candles import sync_daily_candles
from engine.collect.investor import sync_investor_trading
from engine.collect.runner import Progress, SyncResult
from engine.collect.runs import finish_run, record_skipped, start_run
from engine.collect.stocks import sync_stocks
from engine.collect.universe import select_universe, tradable_symbols
from engine.config import Settings
from engine.notify.base import Notifier
from engine.toss.api import TossApi

logger = logging.getLogger(__name__)

JOB = "daily"


@dataclass
class DailyResult:
    trade_date: date
    status: str
    universe: list[str] = field(default_factory=list)
    steps: list[SyncResult] = field(default_factory=list)


async def collect_candles_and_investor(
    api: TossApi,
    session: AsyncSession,
    settings: Settings,
    *,
    as_of: date,
    since: date,
    symbols: list[str] | None = None,
    with_investor: bool = True,
    extend_history: bool = False,
    progress: Progress | None = None,
) -> tuple[list[str], list[SyncResult]]:
    """일봉을 받은 뒤 거래대금으로 대상 종목을 골라 수급을 받는다.

    symbols 를 주면 그 종목만(대상 선정 없이) 처리한다.
    extend_history=True 면 이미 저장된 종목도 since 까지 과거로 넓혀 받는다(백필).
    """
    candle_symbols = symbols or sorted(
        set(await tradable_symbols(session)) | set(settings.collect_always_symbols)
    )
    steps = [
        await sync_daily_candles(
            api,
            session,
            candle_symbols,
            since=since,
            refetch_recent_days=settings.collect_refetch_recent_days,
            extend_history=extend_history,
            concurrency=settings.collect_concurrency,
            progress=progress,
        )
    ]
    universe = symbols or await select_universe(
        session,
        as_of,
        min_avg_trading_value=settings.collect_min_avg_trading_value,
        days=settings.collect_avg_trading_value_days,
    )
    investor_symbols = [s for s in universe if s not in settings.collect_always_symbols]
    if with_investor and investor_symbols:
        steps.append(
            await sync_investor_trading(
                api,
                session,
                investor_symbols,
                since=since,
                refetch_recent_days=settings.collect_refetch_recent_days,
                extend_history=extend_history,
                concurrency=settings.collect_concurrency,
                progress=progress,
            )
        )
    return universe, steps


def _format_failures(trade_date: date, steps: list[SyncResult]) -> str:
    parts = [f"{s.job} 실패 {len(s.failed)}/{s.symbols}종목" for s in steps if s.failed]
    return f"[수집 배치] {trade_date} 일부 실패: " + ", ".join(parts)


async def run_daily_collection(
    api: TossApi,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    notifier: Notifier,
    *,
    trade_date: date,
    progress: Progress | None = None,
) -> DailyResult:
    async with sessions() as session:
        if not await is_trading_day(api, trade_date):
            logger.info("%s 은 휴장일이라 수집을 건너뜁니다", trade_date)
            await record_skipped(session, JOB, trade_date, "market-closed")
            return DailyResult(trade_date, "skipped")

        run_id = await start_run(session, JOB, trade_date)
        try:
            stock_result = await sync_stocks(
                api, session, extra_symbols=settings.collect_always_symbols
            )
            universe, steps = await collect_candles_and_investor(
                api,
                session,
                settings,
                as_of=trade_date,
                since=trade_date - timedelta(days=settings.collect_initial_lookback_days),
                progress=progress,
            )
        except Exception as exc:
            await session.rollback()
            await finish_run(session, run_id, "failed", {"error": repr(exc)[:1000]})
            await notifier.send(f"[수집 배치] {trade_date} 실패: {type(exc).__name__}: {exc}")
            raise

        status = "success" if all(s.ok for s in steps) else "partial"
        await finish_run(
            session,
            run_id,
            status,
            {
                "stocks": stock_result.__dict__,
                "universe_size": len(universe),
                "steps": [s.summary() for s in steps],
            },
        )
        if status == "partial":
            await notifier.send(_format_failures(trade_date, steps))
        logger.info("%s 수집 %s (대상 %d종목)", trade_date, status, len(universe))
        return DailyResult(trade_date, status, universe, steps)
