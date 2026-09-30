from datetime import date, datetime

import httpx
import pytest
import respx

from engine.clock import KST
from engine.toss.pagination import iter_candles, iter_closed_orders, iter_investor_trading
from tests.toss.helpers import BASE_URL, envelope, make_client


def candle(day: int) -> dict[str, str]:
    return {
        "timestamp": f"2026-03-{day:02d}T00:00:00+09:00",
        "openPrice": "100",
        "highPrice": "110",
        "lowPrice": "90",
        "closePrice": "105",
        "volume": "1000",
        "currency": "KRW",
    }


def record(day: int) -> dict[str, object]:
    volume = {"buyVolume": "10", "sellVolume": "4", "netBuyVolume": "6"}
    return {
        "date": f"2026-03-{day:02d}",
        "updatedAt": f"2026-03-{day:02d}T18:00:00+09:00",
        "foreigner": volume,
        "institution": {**volume, "breakdown": None},
    }


@pytest.fixture
def router():
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
        yield mock


async def test_iter_candles_follows_next_before_and_dedupes_inclusive_boundary(router) -> None:
    route = router.get("/api/v1/candles")
    route.side_effect = [
        # nextBefore 가 마지막 봉과 같은 시각(inclusive)이라 다음 페이지 첫 봉이 겹친다
        httpx.Response(
            200,
            json=envelope(
                {"candles": [candle(25), candle(24)], "nextBefore": "2026-03-24T00:00:00+09:00"}
            ),
        ),
        httpx.Response(
            200,
            json=envelope(
                {"candles": [candle(24), candle(23)], "nextBefore": "2026-03-23T00:00:00+09:00"}
            ),
        ),
        httpx.Response(200, json=envelope({"candles": [candle(23)], "nextBefore": None})),
    ]
    client, _, _ = make_client()

    days = [c.timestamp.day async for c in iter_candles(client, "005930", "1d", page_size=2)]

    assert days == [25, 24, 23]
    befores = [c.request.url.params.get("before") for c in route.calls]
    assert befores == [None, "2026-03-24T00:00:00+09:00", "2026-03-23T00:00:00+09:00"]


async def test_iter_candles_stops_at_since(router) -> None:
    route = router.get("/api/v1/candles").respond(
        json=envelope(
            {
                "candles": [candle(25), candle(24), candle(23)],
                "nextBefore": "2026-03-23T00:00:00+09:00",
            }
        )
    )
    client, _, _ = make_client()

    since = datetime(2026, 3, 24, tzinfo=KST)
    days = [c.timestamp.day async for c in iter_candles(client, "005930", "1d", since=since)]

    assert days == [25, 24]
    assert route.call_count == 1


async def test_iter_candles_stops_when_page_has_nothing_new(router) -> None:
    route = router.get("/api/v1/candles").respond(
        json=envelope({"candles": [candle(25)], "nextBefore": "2026-03-25T00:00:00+09:00"})
    )
    client, _, _ = make_client()

    days = [c.timestamp.day async for c in iter_candles(client, "005930", "1d")]

    assert days == [25]
    assert route.call_count == 2


async def test_iter_investor_trading_follows_next_until(router) -> None:
    route = router.get("/api/v1/stocks/005930/investor-trading")
    route.side_effect = [
        httpx.Response(
            200, json=envelope({"records": [record(17), record(16)], "nextUntil": "2026-03-16"})
        ),
        httpx.Response(
            200, json=envelope({"records": [record(16), record(13)], "nextUntil": None})
        ),
    ]
    client, _, _ = make_client()

    days = [r.date.day async for r in iter_investor_trading(client, "005930", page_size=2)]

    assert days == [17, 16, 13]
    assert route.calls[1].request.url.params["until"] == "2026-03-16"


async def test_iter_investor_trading_since(router) -> None:
    router.get("/api/v1/stocks/005930/investor-trading").respond(
        json=envelope({"records": [record(17), record(16), record(13)], "nextUntil": "2026-03-13"})
    )
    client, _, _ = make_client()

    records = [r async for r in iter_investor_trading(client, "005930", since=date(2026, 3, 16))]
    assert [r.date.day for r in records] == [17, 16]


def order(order_id: str) -> dict[str, object]:
    return {
        "orderId": order_id,
        "symbol": "005930",
        "side": "BUY",
        "orderType": "LIMIT",
        "timeInForce": "DAY",
        "status": "FILLED",
        "price": "70000",
        "quantity": "10",
        "currency": "KRW",
        "orderedAt": "2026-03-28T09:30:00+09:00",
        "execution": {"filledQuantity": "10"},
    }


async def test_iter_closed_orders_follows_cursor(router) -> None:
    route = router.get("/api/v1/orders")
    route.side_effect = [
        httpx.Response(
            200, json=envelope({"orders": [order("a")], "nextCursor": "c1", "hasNext": True})
        ),
        httpx.Response(
            200, json=envelope({"orders": [order("b")], "nextCursor": None, "hasNext": False})
        ),
    ]
    client, _, _ = make_client()

    ids = [o.orderId async for o in iter_closed_orders(client, date(2026, 3, 1), date(2026, 3, 31))]

    assert ids == ["a", "b"]
    assert "cursor" not in route.calls[0].request.url.params
    assert route.calls[1].request.url.params["cursor"] == "c1"
