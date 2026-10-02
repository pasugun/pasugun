from datetime import date, timedelta
from decimal import Decimal

from engine.collect.universe import select_universe, tradable_symbols
from engine.db.models import CandleDaily, Stock
from tests.collect.fakes import business_days

AS_OF = date(2026, 9, 29)


def stock(symbol: str, **kw) -> Stock:
    base = {
        "name": symbol,
        "status": "ACTIVE",
        "security_type": "STOCK",
        "is_common_share": True,
        "trading_suspended": False,
        "liquidation_trading": False,
        "source": "toss",
    }
    return Stock(symbol=symbol, **{**base, **kw})


def candles(symbol: str, days: list[date], close: int, volume: int) -> list[CandleDaily]:
    return [
        CandleDaily(
            symbol=symbol,
            date=d,
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=volume,
            adjusted=True,
            source="toss",
        )
        for d in days
    ]


async def test_universe_filters_by_20_day_average_trading_value(session) -> None:
    days = business_days(AS_OF, 25)
    session.add_all(
        [
            stock("AAA"),  # 평균 10,000 × 100,000 = 10억 → 통과(경계 포함)
            stock("BBB"),  # 9.99억 → 탈락
            stock("CCC"),  # 봉 10개뿐(신규 상장) → 탈락
            stock("DDD"),  # 최근 봉이 한 달 전(거래정지 등) → 탈락
            stock("EEE", is_common_share=False),  # 우선주 → 탈락
            stock("FFF", security_type="ETF"),  # ETF → 탈락
            stock("GGG", trading_suspended=True),  # 거래정지 → 탈락
        ]
    )
    session.add_all(candles("AAA", days, 10_000, 100_000))
    session.add_all(candles("BBB", days, 9_990, 100_000))
    session.add_all(candles("CCC", days[:10], 50_000, 1_000_000))
    session.add_all(
        candles("DDD", business_days(AS_OF - timedelta(days=30), 25), 50_000, 1_000_000)
    )
    for symbol in ("EEE", "FFF", "GGG"):
        session.add_all(candles(symbol, days, 50_000, 1_000_000))
    await session.commit()

    universe = await select_universe(session, AS_OF, min_avg_trading_value=1_000_000_000, days=20)

    assert universe == ["AAA"]


async def test_universe_uses_only_last_20_days_up_to_as_of(session) -> None:
    days = business_days(AS_OF, 40)
    session.add(stock("AAA"))
    # 최근 20일은 거래대금 작고, 그 이전 20일은 큼 → 최근 20일만 봐야 탈락
    session.add_all(candles("AAA", days[:20], 1_000, 1_000))
    session.add_all(candles("AAA", days[20:], 100_000, 1_000_000))
    await session.commit()

    assert await select_universe(session, AS_OF, min_avg_trading_value=10**9, days=20) == []
    # as_of 를 과거로 옮기면 그 시점 기준으로 계산한다(미래 데이터 미사용)
    assert await select_universe(session, days[20], min_avg_trading_value=10**9, days=20) == ["AAA"]


async def test_tradable_symbols(session) -> None:
    session.add_all(
        [stock("AAA"), stock("BBB", status="DELISTED"), stock("CCC", liquidation_trading=True)]
    )
    await session.commit()
    assert await tradable_symbols(session) == ["AAA"]
