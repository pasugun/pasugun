"""페이지 순회 헬퍼. TossApi 인터페이스만 쓰므로 실제·mock 클라이언트 모두에 동작한다.

before / until 은 스펙상 inclusive 라서
다음 페이지 첫 항목이 이전 페이지 마지막 항목과 겹칠 수 있다.
이미 본 시각·날짜보다 최신인 항목은 건너뛰고, 새 항목이 하나도 없으면 멈춘다(무한 루프 방지).
"""

from collections.abc import AsyncIterator
from datetime import date, datetime

from engine.toss.api import (
    MAX_CANDLES_PER_REQUEST,
    MAX_INVESTOR_TRADING_PER_REQUEST,
    MAX_ORDERS_PER_PAGE,
    CandleInterval,
    TossApi,
)
from engine.toss.models import Candle, Order, StockInvestorTradingRecord


async def iter_candles(
    api: TossApi,
    symbol: str,
    interval: CandleInterval,
    *,
    since: datetime | None = None,
    before: datetime | None = None,
    adjusted: bool | None = None,
    page_size: int = MAX_CANDLES_PER_REQUEST,
    max_pages: int | None = None,
) -> AsyncIterator[Candle]:
    """최신 → 과거 순으로 캔들을 흘려준다. since 가 있으면 그 시각 이상인 봉까지만."""
    oldest_seen: datetime | None = None
    pages = 0
    while max_pages is None or pages < max_pages:
        page = await api.get_candles(
            symbol, interval, count=page_size, before=before, adjusted=adjusted
        )
        pages += 1
        new = [c for c in page.candles if oldest_seen is None or c.timestamp < oldest_seen]
        for candle in new:
            if since is not None and candle.timestamp < since:
                return
            yield candle
        if not new or page.nextBefore is None:
            return
        oldest_seen = new[-1].timestamp
        before = page.nextBefore


async def iter_investor_trading(
    api: TossApi,
    symbol: str,
    *,
    since: date | None = None,
    until: date | None = None,
    page_size: int = MAX_INVESTOR_TRADING_PER_REQUEST,
    max_pages: int | None = None,
) -> AsyncIterator[StockInvestorTradingRecord]:
    """최신 → 과거 순으로 일별 수급 기록을 흘려준다. since 가 있으면 그 날짜 이상까지만."""
    oldest_seen: date | None = None
    pages = 0
    while max_pages is None or pages < max_pages:
        page = await api.get_investor_trading(symbol, count=page_size, until=until)
        pages += 1
        new = [r for r in page.records if oldest_seen is None or r.date < oldest_seen]
        for record in new:
            if since is not None and record.date < since:
                return
            yield record
        if not new or page.nextUntil is None:
            return
        oldest_seen = new[-1].date
        until = page.nextUntil


async def iter_closed_orders(
    api: TossApi,
    from_: date | None = None,
    to: date | None = None,
    *,
    symbol: str | None = None,
    page_size: int = MAX_ORDERS_PER_PAGE,
) -> AsyncIterator[Order]:
    cursor: str | None = None
    while True:
        page = await api.get_closed_orders(from_, to, cursor=cursor, symbol=symbol, limit=page_size)
        for order in page.orders:
            yield order
        if not page.hasNext or not page.nextCursor or page.nextCursor == cursor:
            return
        cursor = page.nextCursor
