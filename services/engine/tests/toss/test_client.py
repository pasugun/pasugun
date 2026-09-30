from datetime import date, datetime
from decimal import Decimal

import httpx
import pytest
import respx

from engine.clock import KST
from engine.toss.client import TossClient
from engine.toss.errors import TossApiError
from engine.toss.token_manager import InMemoryTokenStore
from tests.toss.helpers import BASE_URL, envelope, error_body, make_client, valid_token

PRICE = {"symbol": "005930", "timestamp": None, "lastPrice": "72000", "currency": "KRW"}


@pytest.fixture
def router():
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as mock:
        yield mock


async def test_get_prices_sends_bearer_token_and_parses_decimal(router) -> None:
    route = router.get("/api/v1/prices").respond(json=envelope([PRICE]))
    client, _, _ = make_client()

    prices = await client.get_prices(["005930", "000660"])

    assert prices[0].lastPrice == Decimal("72000")
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer tok-0"
    assert request.url.params["symbols"] == "005930,000660"
    assert "X-Tossinvest-Account" not in request.headers


async def test_account_apis_add_account_header(router) -> None:
    route = router.get("/api/v1/buying-power").respond(
        json=envelope({"currency": "KRW", "cashBuyingPower": "5000000"})
    )
    client, _, _ = make_client(account_seq="7")

    power = await client.get_buying_power()

    assert power.cashBuyingPower == Decimal("5000000")
    assert route.calls.last.request.headers["X-Tossinvest-Account"] == "7"


async def test_account_api_without_account_seq_fails_before_request(router) -> None:
    client, _, _ = make_client(account_seq=None)
    with pytest.raises(ValueError, match="TOSS_ACCOUNT_SEQ"):
        await client.get_holdings()
    assert not router.calls


async def test_candles_before_encodes_plus_as_percent_2b(router) -> None:
    route = router.get("/api/v1/candles").respond(
        json=envelope({"candles": [], "nextBefore": None})
    )
    client, _, _ = make_client()

    await client.get_candles(
        "005930", "1d", count=200, before=datetime(2026, 3, 24, tzinfo=KST), adjusted=True
    )

    raw_query = route.calls.last.request.url.query.decode()
    assert "before=2026-03-24T00%3A00%3A00%2B09%3A00" in raw_query
    assert "+" not in raw_query
    assert "adjusted=true" in raw_query
    assert "count=200" in raw_query


async def test_expired_token_is_refreshed_once_then_retried(router) -> None:
    route = router.get("/api/v1/prices")
    route.side_effect = [
        httpx.Response(401, json=error_body("expired-token")),
        httpx.Response(200, json=envelope([PRICE])),
    ]
    client, issuer, _ = make_client()

    await client.get_prices(["005930"])

    assert issuer.calls == 1
    assert [c.request.headers["Authorization"] for c in route.calls] == [
        "Bearer tok-0",
        "Bearer tok-1",
    ]


async def test_second_401_after_refresh_raises_without_another_refresh(router) -> None:
    router.get("/api/v1/prices").respond(401, json=error_body("token-revoked"))
    client, issuer, _ = make_client()

    with pytest.raises(TossApiError) as exc:
        await client.get_prices(["005930"])

    assert exc.value.code == "token-revoked"
    assert issuer.calls == 1


async def test_non_refreshable_401_raises_immediately(router) -> None:
    router.get("/api/v1/prices").respond(401, json=error_body("invalid-token"))
    client, issuer, _ = make_client()

    with pytest.raises(TossApiError, match="invalid-token"):
        await client.get_prices(["005930"])
    assert issuer.calls == 0


async def test_429_waits_retry_after_then_succeeds(router) -> None:
    route = router.get("/api/v1/prices")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "3"}, json=error_body("rate-limit-exceeded")),
        httpx.Response(429, headers={"Retry-After": "1"}, json=error_body("rate-limit-exceeded")),
        httpx.Response(200, json=envelope([PRICE])),
    ]
    client, _, clock = make_client()

    await client.get_prices(["005930"])

    # 1회차: max(Retry-After 3, 백오프 1) = 3, 2회차: max(1, 2) = 2
    assert clock.sleeps == [3.0, 2.0]
    assert route.call_count == 3


async def test_429_gives_up_after_three_retries(router) -> None:
    route = router.get("/api/v1/prices").respond(
        429, headers={"Retry-After": "0"}, json=error_body("rate-limit-exceeded", request_id="rq-9")
    )
    client, _, clock = make_client()

    with pytest.raises(TossApiError) as exc:
        await client.get_prices(["005930"])

    assert exc.value.status_code == 429
    assert exc.value.request_id == "rq-9"
    assert route.call_count == 4  # 최초 1회 + 재시도 3회
    assert clock.sleeps == [1.0, 2.0, 4.0]


async def test_error_envelope_becomes_toss_api_error(router) -> None:
    router.get("/api/v1/stocks/999999/warnings").respond(
        404,
        json={
            "error": {
                "requestId": "01HXYZ",
                "code": "stock-not-found",
                "message": "종목을 찾을 수 없습니다.",
                "data": {"field": "symbol"},
            }
        },
    )
    client, _, _ = make_client()

    with pytest.raises(TossApiError) as exc:
        await client.get_warnings("999999")

    err = exc.value
    assert (err.status_code, err.code, err.request_id) == (404, "stock-not-found", "01HXYZ")
    assert err.data == {"field": "symbol"}
    assert "01HXYZ" in str(err)


async def test_non_json_error_uses_request_id_header(router) -> None:
    router.get("/api/v1/prices").respond(502, text="bad gateway", headers={"X-Request-Id": "hdr-1"})
    client, _, _ = make_client()

    with pytest.raises(TossApiError) as exc:
        await client.get_prices(["005930"])

    assert (exc.value.code, exc.value.request_id) == ("unknown", "hdr-1")


async def test_error_message_does_not_contain_token(router) -> None:
    router.get("/api/v1/prices").respond(401, json=error_body("invalid-token"))
    store = InMemoryTokenStore(valid_token("super-secret-token"))
    client, _, _ = make_client(store=store)

    with pytest.raises(TossApiError) as exc:
        await client.get_prices(["005930"])
    assert "super-secret-token" not in str(exc.value)


async def test_rate_limit_headers_are_observed(router) -> None:
    router.get("/api/v1/prices").respond(
        json=envelope([PRICE]), headers={"X-RateLimit-Limit": "15", "X-RateLimit-Remaining": "0"}
    )
    client, _, clock = make_client()

    await client.get_prices(["005930"])
    await client.get_prices(["005930"])

    assert clock.sleeps == [pytest.approx(1 / 15)]


@pytest.mark.parametrize("symbols", [[], [f"{i:06d}" for i in range(201)], ["../x"]])
async def test_invalid_symbols_rejected_before_request(router, symbols) -> None:
    client, _, _ = make_client()
    with pytest.raises(ValueError):
        await client.get_prices(symbols)
    assert not router.calls


async def test_closed_orders_query(router) -> None:
    route = router.get("/api/v1/orders").respond(
        json=envelope({"orders": [], "nextCursor": None, "hasNext": False})
    )
    client, _, _ = make_client()

    await client.get_closed_orders(date(2026, 3, 1), date(2026, 3, 31), cursor="c1")

    params = route.calls.last.request.url.params
    assert dict(params) == {
        "status": "CLOSED",
        "from": "2026-03-01",
        "to": "2026-03-31",
        "cursor": "c1",
        "limit": "100",
    }


def test_client_has_no_order_mutation_methods() -> None:
    """조회 전용: 주문 생성·정정·취소, 조건주문 메서드가 없어야 한다."""
    public = {name for name in dir(TossClient) if not name.startswith("_")}
    forbidden = [
        name
        for name in public
        if any(word in name for word in ("create", "modify", "cancel", "place", "conditional"))
        or (name.startswith(("post", "put", "delete")))
    ]
    assert forbidden == []
    assert not hasattr(TossClient, "_post")
