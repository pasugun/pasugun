from datetime import date

import pytest
from sqlalchemy import func, select

from engine.collect.daily import run_daily_collection
from engine.db.models import CandleDaily, CollectRun, InvestorTradingDaily
from engine.notify.base import RecordingNotifier
from tests.collect.fakes import FakeApi, make_settings

TRADING_DAY = date(2026, 9, 29)  # 화요일
SATURDAY = date(2026, 10, 3)
HOLIDAY = date(2026, 10, 5)  # fixture 의 휴장일(대체공휴일)


async def _count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


async def _runs(session) -> list[tuple[date, str]]:
    rows = await session.execute(
        select(CollectRun.trade_date, CollectRun.status).order_by(CollectRun.id)
    )
    return [tuple(r) for r in rows]


@pytest.mark.parametrize("day", [SATURDAY, HOLIDAY])
async def test_market_closed_day_does_nothing(sessions, session, day) -> None:
    api, notifier = FakeApi(), RecordingNotifier()

    result = await run_daily_collection(api, sessions, make_settings(), notifier, trade_date=day)

    assert result.status == "skipped"
    assert api.candle_requests == []
    assert await _count(session, CandleDaily) == 0
    assert await _runs(session) == [(day, "skipped")]
    assert notifier.messages == []


async def test_trading_day_collects_stocks_candles_and_investor(sessions, session) -> None:
    api, notifier = FakeApi(), RecordingNotifier()

    result = await run_daily_collection(
        api, sessions, make_settings(), notifier, trade_date=TRADING_DAY
    )

    assert result.status == "success"
    assert result.universe == ["000660", "005930"]  # ETF(069500)는 대상 선정에서 빠진다
    assert await _count(session, CandleDaily) == 90  # 3종목 × 30일(벤치마크 포함)
    assert await _count(session, InvestorTradingDaily) == 60
    assert await _runs(session) == [(TRADING_DAY, "success")]


async def test_running_twice_gives_same_data(sessions, session) -> None:
    api, notifier = FakeApi(), RecordingNotifier()
    settings = make_settings()

    await run_daily_collection(api, sessions, settings, notifier, trade_date=TRADING_DAY)
    snapshot = list(
        await session.execute(select(CandleDaily.symbol, CandleDaily.date, CandleDaily.close))
    )
    await run_daily_collection(api, sessions, settings, notifier, trade_date=TRADING_DAY)

    again = list(
        await session.execute(select(CandleDaily.symbol, CandleDaily.date, CandleDaily.close))
    )
    assert sorted(again) == sorted(snapshot)
    assert await _count(session, InvestorTradingDaily) == 60


async def test_partial_failure_is_recorded_and_notified(sessions, session) -> None:
    api, notifier = FakeApi(), RecordingNotifier()
    api.fail_candles.add("000660")

    result = await run_daily_collection(
        api, sessions, make_settings(), notifier, trade_date=TRADING_DAY
    )

    assert result.status == "partial"
    assert await _runs(session) == [(TRADING_DAY, "partial")]
    assert len(notifier.messages) == 1
    assert "candles 실패 1/3" in notifier.messages[0]


async def test_unexpected_error_marks_run_failed_and_notifies(sessions, session) -> None:
    class BrokenApi(FakeApi):
        async def get_stocks_all(self, market, **kw):
            raise RuntimeError("boom")

    notifier = RecordingNotifier()
    with pytest.raises(RuntimeError):
        await run_daily_collection(
            BrokenApi(), sessions, make_settings(), notifier, trade_date=TRADING_DAY
        )

    assert await _runs(session) == [(TRADING_DAY, "failed")]
    assert "실패" in notifier.messages[0]
