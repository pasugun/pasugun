from datetime import date

from sqlalchemy import func, select

from engine.collect.investor import sync_investor_trading
from engine.db.models import InvestorTradingDaily
from engine.toss.models import StockInvestorTradingPage
from tests.collect.fakes import FakeApi


async def _sync(api, session):
    return await sync_investor_trading(
        api, session, ["005930"], since=date(2026, 1, 1), refetch_recent_days=5, concurrency=1
    )


async def test_sync_is_idempotent(session) -> None:
    api = FakeApi()
    first = await _sync(api, session)
    second = await _sync(api, session)

    assert first.rows == 30
    assert second.rows == 5
    count = await session.scalar(select(func.count()).select_from(InvestorTradingDaily))
    assert count == 30


class ProvisionalTodayApi(FakeApi):
    """당일 기록은 개인(individual) 잠정치가 없는 상황."""

    def __init__(self, provisional: bool) -> None:
        super().__init__()
        self.provisional = provisional

    async def get_investor_trading(self, symbol, *, count=None, until=None):
        page = await super().get_investor_trading(symbol, count=count, until=until)
        if not self.provisional or until is not None:
            return page
        latest = page.records[0].model_copy(update={"individual": None})
        return StockInvestorTradingPage(
            nextUntil=page.nextUntil, records=[latest, *page.records[1:]]
        )


async def test_provisional_null_is_filled_by_next_run(session) -> None:
    await _sync(ProvisionalTodayApi(provisional=True), session)
    latest = await session.get(
        InvestorTradingDaily, {"symbol": "005930", "date": date(2026, 9, 29)}
    )
    assert latest.individual_net is None
    assert latest.foreigner_net is not None

    await _sync(ProvisionalTodayApi(provisional=False), session)

    await session.refresh(latest)
    assert latest.individual_net is not None
