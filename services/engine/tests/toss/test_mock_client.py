from datetime import date

import pytest

from engine.config import Settings, TossMode
from engine.toss.errors import TossApiError
from engine.toss.factory import create_toss_api
from engine.toss.mock import MockTossClient
from engine.toss.pagination import iter_candles, iter_closed_orders, iter_investor_trading


@pytest.fixture
def mock_api() -> MockTossClient:
    api = create_toss_api(Settings(_env_file=None, toss_mode=TossMode.MOCK))
    assert isinstance(api, MockTossClient)
    return api


async def test_every_fixture_parses_with_response_models(mock_api: MockTossClient) -> None:
    assert (await mock_api.get_accounts())[0].accountSeq == 1
    assert {s.symbol for s in await mock_api.get_stocks(["005930", "069500"])} == {
        "005930",
        "069500",
    }
    assert await mock_api.get_stocks_all("KOSPI")
    assert await mock_api.get_warnings("000660")
    assert (await mock_api.get_prices(["005930"]))[0].symbol == "005930"
    assert (await mock_api.get_candles("005930", "1d", count=3)).candles
    assert (await mock_api.get_investor_trading("005930", count=3)).records
    assert (await mock_api.get_rankings("MARKET_TRADING_AMOUNT", "KR", "1d")).rankings
    assert (await mock_api.get_market_calendar_kr()).today.is_open
    assert (await mock_api.get_holdings()).items
    assert (await mock_api.get_buying_power()).currency == "KRW"
    assert (await mock_api.get_closed_orders()).orders


async def test_mock_candles_paginate_through_all_30_days(mock_api: MockTossClient) -> None:
    candles = [c async for c in iter_candles(mock_api, "005930", "1d", page_size=7)]

    assert len(candles) == 30
    timestamps = [c.timestamp for c in candles]
    assert timestamps == sorted(timestamps, reverse=True)
    assert len(set(timestamps)) == 30


async def test_mock_candle_values_are_consistent(mock_api: MockTossClient) -> None:
    async for c in iter_candles(mock_api, "000660", "1d"):
        assert c.lowPrice <= min(c.openPrice, c.closePrice)
        assert c.highPrice >= max(c.openPrice, c.closePrice)


async def test_mock_investor_trading_paginates(mock_api: MockTossClient) -> None:
    records = [r async for r in iter_investor_trading(mock_api, "005930", page_size=4)]
    assert len(records) == 30


async def test_mock_closed_orders_paginate_and_filter(mock_api: MockTossClient) -> None:
    orders = [o async for o in iter_closed_orders(mock_api, page_size=1)]
    assert len(orders) == 2

    march_30 = [o async for o in iter_closed_orders(mock_api, date(2026, 3, 30), date(2026, 3, 30))]
    assert [o.orderId for o in march_30] == ["mock-order-2"]


async def test_mock_unknown_symbol_raises_not_found(mock_api: MockTossClient) -> None:
    with pytest.raises(TossApiError) as exc:
        await mock_api.get_candles("999999", "1d")
    assert exc.value.code == "stock-not-found"


def test_live_mode_requires_credentials() -> None:
    with pytest.raises(RuntimeError, match="TOSS_CLIENT_ID"):
        create_toss_api(Settings(_env_file=None, toss_mode=TossMode.LIVE))
