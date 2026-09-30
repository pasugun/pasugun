"""토스증권 Open API 조회 전용 HTTP 클라이언트.

주문 생성·정정·취소, 조건주문 엔드포인트는 만들지 않는다. 요청은 GET(_get)만 보낼 수 있다.
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import date, datetime
from typing import Any

import httpx
from pydantic import TypeAdapter

from engine.toss.api import (
    MAX_CANDLES_PER_REQUEST,
    MAX_INVESTOR_TRADING_PER_REQUEST,
    MAX_ORDERS_PER_PAGE,
    CandleInterval,
    check_symbol,
    check_symbols,
)
from engine.toss.errors import TossApiError
from engine.toss.models import (
    Account,
    BuyingPower,
    CandlePage,
    Holdings,
    KrMarketCalendar,
    OrderPage,
    Price,
    RankingPage,
    Stock,
    StockInvestorTradingPage,
    StockListItem,
    StockWarning,
)
from engine.toss.rate_limit import RateLimiter, RateLimitGroup
from engine.toss.token_manager import TokenManager

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openapi.tossinvest.com"
MAX_RATE_LIMIT_RETRIES = 3
# 이 코드의 401 은 토큰을 새로 받으면 풀린다. 한 요청당 재발급은 한 번만 한다.
REFRESHABLE_AUTH_CODES = frozenset({"expired-token", "token-revoked"})

Sleep = Callable[[float], Awaitable[None]]


def _params(**values: Any) -> dict[str, str]:
    """None 은 빼고 쿼리 문자열로 바꾼다. httpx 가 '+' 를 %2B 로 인코딩한다."""
    out: dict[str, str] = {}
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        elif isinstance(value, datetime | date):
            out[key] = value.isoformat()
        else:
            out[key] = str(value)
    return out


def _retry_after_seconds(response: httpx.Response) -> float:
    try:
        return max(0.0, float(response.headers.get("Retry-After", "0")))
    except ValueError:
        return 0.0


class TossClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        token_manager: TokenManager,
        rate_limiter: RateLimiter,
        *,
        account_seq: str | None = None,
        sleep: Sleep = asyncio.sleep,
        jitter: Callable[[], float] = lambda: random.uniform(0, 0.25),
    ) -> None:
        self._http = http
        self._tokens = token_manager
        self._limiter = rate_limiter
        self._account_seq = account_seq
        self._sleep = sleep
        self._jitter = jitter

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- 공통 요청 -------------------------------------------------------------

    def _account_header(self) -> dict[str, str]:
        if not self._account_seq:
            raise ValueError(
                "TOSS_ACCOUNT_SEQ 가 설정되지 않았습니다 (GET /api/v1/accounts 로 확인)"
            )
        return {"X-Tossinvest-Account": self._account_seq}

    async def _get(
        self,
        path: str,
        group: RateLimitGroup,
        *,
        params: Mapping[str, str] | None = None,
        account: bool = False,
    ) -> Any:
        """GET 요청 후 envelope 의 result 를 돌려준다.

        - 429: Retry-After 와 지수 백오프(1s, 2s, 4s) 중 큰 값만큼 기다렸다 최대 3회 재시도
        - 401 expired-token / token-revoked: 토큰을 한 번만 재발급하고 재시도
        """
        extra_headers = self._account_header() if account else {}
        refreshed = False
        rate_limit_retries = 0

        while True:
            await self._limiter.acquire(group)
            token = await self._tokens.get_token()
            response = await self._http.get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {token}", **extra_headers},
            )
            self._limiter.observe(group, response.headers)

            if response.status_code == 429 and rate_limit_retries < MAX_RATE_LIMIT_RETRIES:
                delay = max(_retry_after_seconds(response), 2.0**rate_limit_retries)
                delay += self._jitter()
                rate_limit_retries += 1
                logger.warning(
                    "토스 429 (%s %s), %.2f초 후 재시도 %d/%d",
                    group,
                    path,
                    delay,
                    rate_limit_retries,
                    MAX_RATE_LIMIT_RETRIES,
                )
                await self._sleep(delay)
                continue

            if response.status_code == 401 and not refreshed:
                error = TossApiError.from_response(response)
                if error.code in REFRESHABLE_AUTH_CODES:
                    refreshed = True
                    logger.info("토스 토큰 %s, 재발급 후 재시도합니다", error.code)
                    await self._tokens.refresh(stale_token=token)
                    continue
                raise error

            if response.is_success:
                return response.json()["result"]
            raise TossApiError.from_response(response)

    async def _get_model(self, model: Any, path: str, group: RateLimitGroup, **kw: Any) -> Any:
        return TypeAdapter(model).validate_python(await self._get(path, group, **kw))

    # --- Account / Asset / Order Info / Order History -------------------------

    async def get_accounts(self) -> list[Account]:
        return await self._get_model(list[Account], "/api/v1/accounts", RateLimitGroup.ACCOUNT)

    async def get_holdings(self, symbol: str | None = None) -> Holdings:
        return await self._get_model(
            Holdings,
            "/api/v1/holdings",
            RateLimitGroup.ASSET,
            params=_params(symbol=symbol),
            account=True,
        )

    async def get_buying_power(self, currency: str = "KRW") -> BuyingPower:
        return await self._get_model(
            BuyingPower,
            "/api/v1/buying-power",
            RateLimitGroup.ORDER_INFO,
            params=_params(currency=currency),
            account=True,
        )

    async def get_closed_orders(
        self,
        from_: date | None = None,
        to: date | None = None,
        *,
        cursor: str | None = None,
        symbol: str | None = None,
        limit: int | None = MAX_ORDERS_PER_PAGE,
    ) -> OrderPage:
        """종료된 주문(체결·취소 등) 한 페이지. 전체 순회는 pagination.iter_closed_orders."""
        return await self._get_model(
            OrderPage,
            "/api/v1/orders",
            RateLimitGroup.ORDER_HISTORY,
            params=_params(
                status="CLOSED",
                symbol=symbol,
                cursor=cursor,
                limit=limit,
                **{"from": from_, "to": to},
            ),
            account=True,
        )

    # --- Stock Info -----------------------------------------------------------

    async def get_stocks_all(
        self,
        market: str,
        *,
        status: str | None = None,
        security_type: str | None = None,
        common_share: bool | None = None,
    ) -> list[StockListItem]:
        return await self._get_model(
            list[StockListItem],
            "/api/v1/stocks/all",
            RateLimitGroup.STOCK_ALL,
            params=_params(
                market=market, status=status, securityType=security_type, commonShare=common_share
            ),
        )

    async def get_stocks(self, symbols: Sequence[str]) -> list[Stock]:
        return await self._get_model(
            list[Stock],
            "/api/v1/stocks",
            RateLimitGroup.STOCK,
            params=_params(symbols=check_symbols(symbols)),
        )

    async def get_warnings(self, symbol: str) -> list[StockWarning]:
        return await self._get_model(
            list[StockWarning],
            f"/api/v1/stocks/{check_symbol(symbol)}/warnings",
            RateLimitGroup.STOCK,
        )

    async def get_investor_trading(
        self, symbol: str, *, count: int | None = None, until: date | None = None
    ) -> StockInvestorTradingPage:
        if count is not None and not 1 <= count <= MAX_INVESTOR_TRADING_PER_REQUEST:
            raise ValueError(f"count 는 1~{MAX_INVESTOR_TRADING_PER_REQUEST} 입니다")
        return await self._get_model(
            StockInvestorTradingPage,
            f"/api/v1/stocks/{check_symbol(symbol)}/investor-trading",
            RateLimitGroup.STOCK_TRADING_TREND,
            params=_params(count=count, until=until),
        )

    # --- Market Data ----------------------------------------------------------

    async def get_prices(self, symbols: Sequence[str]) -> list[Price]:
        return await self._get_model(
            list[Price],
            "/api/v1/prices",
            RateLimitGroup.MARKET_DATA,
            params=_params(symbols=check_symbols(symbols)),
        )

    async def get_candles(
        self,
        symbol: str,
        interval: CandleInterval,
        *,
        count: int | None = None,
        before: datetime | None = None,
        adjusted: bool | None = None,
    ) -> CandlePage:
        """캔들 한 페이지(최신순). 과거로 순회하려면 pagination.iter_candles."""
        if count is not None and not 1 <= count <= MAX_CANDLES_PER_REQUEST:
            raise ValueError(f"count 는 1~{MAX_CANDLES_PER_REQUEST} 입니다")
        return await self._get_model(
            CandlePage,
            "/api/v1/candles",
            RateLimitGroup.MARKET_DATA_CHART,
            params=_params(
                symbol=symbol, interval=interval, count=count, before=before, adjusted=adjusted
            ),
        )

    # --- Market Info / Ranking ------------------------------------------------

    async def get_market_calendar_kr(self, day: date | None = None) -> KrMarketCalendar:
        return await self._get_model(
            KrMarketCalendar,
            "/api/v1/market-calendar/KR",
            RateLimitGroup.MARKET_INFO,
            params=_params(date=day),
        )

    async def get_rankings(
        self,
        type: str,
        market_country: str,
        duration: str,
        *,
        exclude_investment_caution: bool | None = None,
        count: int | None = None,
    ) -> RankingPage:
        return await self._get_model(
            RankingPage,
            "/api/v1/rankings",
            RateLimitGroup.RANKING,
            params=_params(
                type=type,
                marketCountry=market_country,
                duration=duration,
                excludeInvestmentCaution=exclude_investment_caution,
                count=count,
            ),
        )
