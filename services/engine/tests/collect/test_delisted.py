from datetime import date
from decimal import Decimal

from sqlalchemy import select

from engine.collect.delisted import (
    DelistedStock,
    import_delisted,
    looks_like_common_share,
    month_ends,
)
from engine.db.models import CandleDaily, InvestorTradingDaily, Stock


class FakeSource:
    def list_delisted(self, start, end, active_symbols):
        return [
            DelistedStock(s, f"폐지{s}", "KOSDAQ", date(2025, 6, 30))
            for s in ("900000", "005930")
            if s not in active_symbols
        ]

    def candles(self, symbol, start, end):
        return [
            {
                "symbol": symbol,
                "date": date(2025, 6, 30),
                "open": Decimal(1),
                "high": Decimal(1),
                "low": Decimal(1),
                "close": Decimal(1),
                "volume": 10,
                "adjusted": False,
                "source": "pykrx",
            }
        ]

    def investor(self, symbol, start, end):
        return [
            {
                "symbol": symbol,
                "date": date(2025, 6, 30),
                "foreigner_net": 1,
                "institution_net": 2,
                "individual_net": -3,
                "source": "pykrx",
            }
        ]


async def _run_sync(fn, *args):
    return fn(*args)


async def test_delisted_rows_are_added_with_source_and_never_override_toss(session) -> None:
    toss_row = CandleDaily(
        symbol="900000",
        date=date(2025, 6, 30),
        open=Decimal(5),
        high=Decimal(5),
        low=Decimal(5),
        close=Decimal(5),
        volume=5,
        adjusted=True,
        source="toss",
    )
    session.add(toss_row)
    await session.commit()

    await import_delisted(
        FakeSource(),
        session,
        start=date(2023, 1, 1),
        end=date(2026, 9, 29),
        active_symbols={"005930"},
        with_investor=True,
        run_sync=_run_sync,
    )

    stock = await session.get(Stock, "900000")
    assert (stock.status, stock.source, stock.delist_date) == (
        "DELISTED",
        "pykrx",
        date(2025, 6, 30),
    )
    await session.refresh(toss_row)
    assert (toss_row.close, toss_row.source) == (Decimal(5), "toss")  # 덮어쓰지 않음
    inv = (await session.scalars(select(InvestorTradingDaily))).all()
    assert [(r.symbol, r.source) for r in inv] == [("900000", "pykrx")]
    assert await session.get(Stock, "005930") is None  # 현재 상장 종목은 건드리지 않음


def test_month_ends() -> None:
    assert month_ends(date(2025, 11, 15), date(2026, 2, 10)) == [
        date(2025, 11, 30),
        date(2025, 12, 31),
        date(2026, 1, 31),
        date(2026, 2, 10),
    ]


def test_common_share_heuristic() -> None:
    assert looks_like_common_share("005930")
    assert not looks_like_common_share("005935")  # 삼성전자우
    assert not looks_like_common_share("0101N0")
