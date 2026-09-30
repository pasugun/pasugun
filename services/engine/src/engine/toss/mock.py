"""TOSS_MODE=mock 에서 쓰는 가짜 클라이언트.

tests/fixtures/toss/*.json 을 실제 응답 모델로 파싱해 돌려준다.

네트워크를 쓰지 않는다. 캔들·수급·체결 주문은 실제 API 처럼 before/until/cursor 로 페이지를 나눈다.
"""

import json
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from engine.toss.api import CandleInterval, check_symbol, check_symbols
from engine.toss.errors import TossApiError
from engine.toss.models import (
    Account,
    BuyingPower,
    Candle,
    CandlePage,
    Holdings,
    KrMarketCalendar,
    Order,
    OrderPage,
    Price,
    RankingPage,
    Stock,
    StockInvestorTradingPage,
    StockInvestorTradingRecord,
    StockListItem,
    StockWarning,
)

DEFAULT_CANDLE_COUNT = 100
DEFAULT_INVESTOR_TRADING_COUNT = 10
DEFAULT_ORDER_LIMIT = 20


def _not_found(symbol: str) -> TossApiError:
    return TossApiError(404, "stock-not-found", f"종목을 찾을 수 없습니다: {symbol}", "mock")


class MockTossClient:
    def __init__(self, fixtures_dir: Path) -> None:
        self._dir = fixtures_dir
        self._cache: dict[str, Any] = {}

    async def aclose(self) -> None:
        return None

    def _load(self, name: str) -> Any:
        if name not in self._cache:
            path = self._dir / f"{name}.json"
            self._cache[name] = json.loads(path.read_text(encoding="utf-8"))
        return self._cache[name]

    def _parse(self, model: Any, name: str) -> Any:
        return TypeAdapter(model).validate_python(self._load(name))

    # --- Account / Asset / Order ---------------------------------------------

    async def get_accounts(self) -> list[Account]:
        return self._parse(list[Account], "accounts")

    async def get_holdings(self, symbol: str | None = None) -> Holdings:
        holdings: Holdings = self._parse(Holdings, "holdings")
        if symbol is None:
            return holdings
        # TODO: 실제 API 는 symbol 필터 시 요약 필드도 재계산한다. mock 은 items 만 거른다.
        return holdings.model_copy(
            update={"items": [i for i in holdings.items if i.symbol == symbol]}
        )

    async def get_buying_power(self, currency: str = "KRW") -> BuyingPower:
        return self._parse(BuyingPower, "buying_power")

    async def get_closed_orders(
        self,
        from_: date | None = None,
        to: date | None = None,
        *,
        cursor: str | None = None,
        symbol: str | None = None,
        limit: int | None = None,
    ) -> OrderPage:
        orders: list[Order] = self._parse(list[Order], "orders_closed")
        orders = [
            o
            for o in orders
            if (symbol is None or o.symbol == symbol)
            and (from_ is None or o.orderedAt.date() >= from_)
            and (to is None or o.orderedAt.date() <= to)
        ]
        start = int(cursor) if cursor else 0
        end = start + (limit or DEFAULT_ORDER_LIMIT)
        has_next = end < len(orders)
        return OrderPage(
            orders=orders[start:end], nextCursor=str(end) if has_next else None, hasNext=has_next
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
        items = TypeAdapter(list[StockListItem]).validate_python(
            self._load("stocks_all").get(market, [])
        )
        return [
            i
            for i in items
            if (security_type is None or i.securityType == security_type)
            and (common_share is None or i.isCommonShare == common_share)
        ]

    async def get_stocks(self, symbols: Sequence[str]) -> list[Stock]:
        check_symbols(symbols)
        wanted = set(symbols)
        return [s for s in self._parse(list[Stock], "stocks") if s.symbol in wanted]

    async def get_warnings(self, symbol: str) -> list[StockWarning]:
        by_symbol = self._load("warnings")
        if check_symbol(symbol) not in by_symbol:
            raise _not_found(symbol)
        return TypeAdapter(list[StockWarning]).validate_python(by_symbol[symbol])

    async def get_investor_trading(
        self, symbol: str, *, count: int | None = None, until: date | None = None
    ) -> StockInvestorTradingPage:
        by_symbol = self._load("investor_trading")
        if check_symbol(symbol) not in by_symbol:
            raise _not_found(symbol)
        records = TypeAdapter(list[StockInvestorTradingRecord]).validate_python(by_symbol[symbol])
        eligible = [r for r in records if until is None or r.date <= until]
        page = eligible[: count or DEFAULT_INVESTOR_TRADING_COUNT]
        has_more = len(eligible) > len(page)
        # 실제 스펙 예시처럼 다음 기준일은 inclusive 로 준다(겹침은 순회 헬퍼가 걸러낸다).
        return StockInvestorTradingPage(
            nextUntil=page[-1].date if page and has_more else None, records=page
        )

    # --- Market Data ----------------------------------------------------------

    async def get_prices(self, symbols: Sequence[str]) -> list[Price]:
        check_symbols(symbols)
        wanted = set(symbols)
        return [p for p in self._parse(list[Price], "prices") if p.symbol in wanted]

    async def get_candles(
        self,
        symbol: str,
        interval: CandleInterval,
        *,
        count: int | None = None,
        before: datetime | None = None,
        adjusted: bool | None = None,
    ) -> CandlePage:
        if interval != "1d":
            raise TossApiError(400, "invalid-request", "mock 은 일봉(1d)만 제공합니다", "mock")
        by_symbol = self._load("candles_1d")
        if check_symbol(symbol) not in by_symbol:
            raise _not_found(symbol)
        candles = TypeAdapter(list[Candle]).validate_python(by_symbol[symbol])
        eligible = [c for c in candles if before is None or c.timestamp <= before]
        page = eligible[: count or DEFAULT_CANDLE_COUNT]
        has_more = len(eligible) > len(page)
        return CandlePage(
            candles=page, nextBefore=page[-1].timestamp if page and has_more else None
        )

    # --- Market Info / Ranking ------------------------------------------------

    async def get_market_calendar_kr(self, day: date | None = None) -> KrMarketCalendar:
        # TODO(3단계): 휴장일 시나리오가 필요하면 날짜별 fixture 로 확장
        return self._parse(KrMarketCalendar, "market_calendar_kr")

    async def get_rankings(
        self,
        type: str,
        market_country: str,
        duration: str,
        *,
        exclude_investment_caution: bool | None = None,
        count: int | None = None,
    ) -> RankingPage:
        page: RankingPage = self._parse(RankingPage, "rankings")
        return page.model_copy(update={"rankings": page.rankings[: count or 100]})
