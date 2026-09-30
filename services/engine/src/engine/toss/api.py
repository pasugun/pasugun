"""토스 조회 API 인터페이스. 실제 클라이언트(TossClient)와 MockTossClient 가 같은 모양을 따른다."""

import re
from collections.abc import Sequence
from datetime import date, datetime
from typing import Literal, Protocol

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

MAX_SYMBOLS_PER_REQUEST = 200
MAX_CANDLES_PER_REQUEST = 200
MAX_INVESTOR_TRADING_PER_REQUEST = 100
MAX_ORDERS_PER_PAGE = 100

CandleInterval = Literal["1m", "1d"]


# 스펙: "영문 대/소문자, 숫자, '.', '-' 만 허용한다."
SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9.\-]+$")


def check_symbol(symbol: str) -> str:
    if not SYMBOL_PATTERN.fullmatch(symbol) or symbol in {".", ".."}:
        raise ValueError(f"잘못된 종목 심볼입니다: {symbol!r}")
    return symbol


def check_symbols(symbols: Sequence[str]) -> str:
    for symbol in symbols:
        check_symbol(symbol)
    if not symbols:
        raise ValueError("symbols 가 비어 있습니다")
    if len(symbols) > MAX_SYMBOLS_PER_REQUEST:
        raise ValueError(
            f"symbols 는 최대 {MAX_SYMBOLS_PER_REQUEST}개입니다 (받은 개수: {len(symbols)})"
        )
    return ",".join(symbols)


class TossApi(Protocol):
    async def get_accounts(self) -> list[Account]: ...

    async def get_stocks_all(
        self,
        market: str,
        *,
        status: str | None = None,
        security_type: str | None = None,
        common_share: bool | None = None,
    ) -> list[StockListItem]: ...

    async def get_stocks(self, symbols: Sequence[str]) -> list[Stock]: ...

    async def get_warnings(self, symbol: str) -> list[StockWarning]: ...

    async def get_prices(self, symbols: Sequence[str]) -> list[Price]: ...

    async def get_candles(
        self,
        symbol: str,
        interval: CandleInterval,
        *,
        count: int | None = None,
        before: datetime | None = None,
        adjusted: bool | None = None,
    ) -> CandlePage: ...

    async def get_investor_trading(
        self, symbol: str, *, count: int | None = None, until: date | None = None
    ) -> StockInvestorTradingPage: ...

    async def get_rankings(
        self,
        type: str,
        market_country: str,
        duration: str,
        *,
        exclude_investment_caution: bool | None = None,
        count: int | None = None,
    ) -> RankingPage: ...

    async def get_market_calendar_kr(self, day: date | None = None) -> KrMarketCalendar: ...

    async def get_holdings(self, symbol: str | None = None) -> Holdings: ...

    async def get_buying_power(self, currency: str = "KRW") -> BuyingPower: ...

    async def get_closed_orders(
        self,
        from_: date | None = None,
        to: date | None = None,
        *,
        cursor: str | None = None,
        symbol: str | None = None,
        limit: int | None = None,
    ) -> OrderPage: ...

    async def aclose(self) -> None: ...
