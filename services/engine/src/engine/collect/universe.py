"""수집·감시 대상 종목 선정."""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from engine.db.models import CandleDaily, Stock

# 최근 봉이 이보다 오래됐으면(거래정지 등) 대상에서 뺀다
STALE_AFTER = timedelta(days=10)


async def tradable_symbols(session: AsyncSession) -> list[str]:
    """보통주 + 주식(ETF 등 제외) + 상장 + 거래정지·정리매매 아님."""
    rows = await session.scalars(
        select(Stock.symbol)
        .where(
            Stock.status == "ACTIVE",
            Stock.security_type == "STOCK",
            Stock.is_common_share.is_(True),
            Stock.trading_suspended.is_(False),
            Stock.liquidation_trading.is_(False),
        )
        .order_by(Stock.symbol)
    )
    return list(rows)


async def select_universe(
    session: AsyncSession,
    as_of: date,
    *,
    min_avg_trading_value: int,
    days: int,
) -> list[str]:
    """tradable_symbols 중 as_of 까지 최근 days 거래일 평균 거래대금(종가×거래량) ≥ 기준인 종목.

    토스 일봉은 거래대금을 주지 않아 종가×거래량으로 근사한다.
    봉이 days 개 미만(신규 상장)이면 제외.
    """
    ranked = (
        select(
            CandleDaily.symbol,
            CandleDaily.date,
            (CandleDaily.close * CandleDaily.volume).label("value"),
            func.row_number()
            .over(partition_by=CandleDaily.symbol, order_by=CandleDaily.date.desc())
            .label("rn"),
        )
        .where(CandleDaily.date <= as_of, CandleDaily.date > as_of - timedelta(days=days * 3))
        .subquery()
    )
    tradable = set(await tradable_symbols(session))
    rows = await session.execute(
        select(ranked.c.symbol)
        .where(ranked.c.rn <= days)
        .group_by(ranked.c.symbol)
        .having(
            func.count() == days,
            func.avg(ranked.c.value) >= Decimal(min_avg_trading_value),
            func.max(ranked.c.date) >= as_of - STALE_AFTER,
        )
        .order_by(ranked.c.symbol)
    )
    return [symbol for (symbol,) in rows if symbol in tradable]
