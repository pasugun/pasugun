"""DB 에서 시계열을 읽고, 결과를 backtest_runs / backtest_trades 에 저장한다."""

from collections.abc import AsyncIterator
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from engine.backtest.runner import BacktestReport
from engine.db.models import BacktestRun, BacktestTrade, CandleDaily, InvestorTradingDaily, Stock
from engine.robots.base import jsonable
from engine.robots.data import Bar, InvestorFlow, SymbolSeries


async def backtest_symbols(session: AsyncSession) -> list[str]:
    """보통주 주식 전체. 상장폐지(pykrx 보충분 포함)도 넣어 생존자 편향을 줄인다.

    현재 상태(거래정지 등)로는 거르지 않는다 — 과거 시점에는 정상이었을 수 있다.
    """
    rows = await session.scalars(
        select(Stock.symbol)
        .where(
            Stock.security_type == "STOCK",
            Stock.is_common_share.is_(True),
            or_(Stock.status.in_(("ACTIVE", "DELISTED", "MISSING")), Stock.status.is_(None)),
        )
        .order_by(Stock.symbol)
    )
    return list(rows)


async def load_series(
    session: AsyncSession, symbol: str, start: date, end: date, *, with_flows: bool = True
) -> SymbolSeries:
    candles = await session.scalars(
        select(CandleDaily)
        .where(CandleDaily.symbol == symbol, CandleDaily.date >= start, CandleDaily.date <= end)
        .order_by(CandleDaily.date)
    )
    bars = [Bar(c.date, c.open, c.high, c.low, c.close, c.volume) for c in candles]
    flows: dict[date, InvestorFlow] = {}
    if with_flows:
        rows = await session.scalars(
            select(InvestorTradingDaily).where(
                InvestorTradingDaily.symbol == symbol,
                InvestorTradingDaily.date >= start,
                InvestorTradingDaily.date <= end,
            )
        )
        flows = {
            r.date: InvestorFlow(r.date, r.foreigner_net, r.institution_net, r.individual_net)
            for r in rows
        }
    return SymbolSeries(symbol, bars, flows)


async def iter_series(
    session: AsyncSession, symbols: list[str], start: date, end: date
) -> AsyncIterator[SymbolSeries]:
    for symbol in symbols:
        yield await load_series(session, symbol, start, end)


async def save_report(session: AsyncSession, report: BacktestReport) -> int:
    run = BacktestRun(
        robot_id=report.robot_id,
        params=jsonable({"strategy": report.params, "config": report.config.model_dump()}),
        period_start=report.start,
        period_end=report.end,
        split_date=report.split,
        metrics=report.metrics_json(),
    )
    session.add(run)
    await session.flush()
    for seg in report.segments:
        for t in seg.sim.trades:
            session.add(
                BacktestTrade(
                    run_id=run.id,
                    symbol=t.symbol,
                    entry_date=t.entry_date,
                    entry_price=t.entry_price,
                    exit_date=t.exit_date,
                    exit_price=t.exit_price,
                    return_pct=t.net_return * 100,
                    exit_reason=t.exit_reason,
                    segment=seg.segment.name,
                )
            )
    await session.commit()
    return run.id
