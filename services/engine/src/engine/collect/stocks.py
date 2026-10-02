"""종목 마스터 동기화 (GET /api/v1/stocks/all + /api/v1/stocks)."""

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import islice
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from engine.collect.db import upsert
from engine.db.models import Stock as StockRow
from engine.toss.api import MAX_SYMBOLS_PER_REQUEST, TossApi
from engine.toss.models import Stock, StockListItem

logger = logging.getLogger(__name__)

MARKETS = ("KOSPI", "KOSDAQ")
MISSING_STATUS = "MISSING"  # 목록에서 빠졌는데 상세 조회로도 확인되지 않은 종목

STOCK_UPDATE_COLUMNS = (
    "name",
    "market",
    "status",
    "security_type",
    "is_common_share",
    "trading_suspended",
    "liquidation_trading",
    "list_date",
    "delist_date",
    "source",
)


@dataclass(frozen=True)
class StockSyncResult:
    listed: int
    upserted: int
    delisted_or_missing: int


def _batches(items: Sequence[str], size: int = MAX_SYMBOLS_PER_REQUEST) -> Iterable[list[str]]:
    it = iter(items)
    while batch := list(islice(it, size)):
        yield batch


async def _fetch_details(api: TossApi, symbols: Sequence[str]) -> dict[str, Stock]:
    details: dict[str, Stock] = {}
    for batch in _batches(symbols):
        for stock in await api.get_stocks(batch):
            details[stock.symbol] = stock
    return details


def _row(item: StockListItem | None, detail: Stock | None, market: str | None) -> dict[str, Any]:
    if detail is not None:
        kr = detail.koreanMarketDetail
        return {
            "symbol": detail.symbol,
            "name": detail.name,
            "market": detail.market,
            "status": detail.status,
            "security_type": detail.securityType,
            "is_common_share": detail.isCommonShare,
            "trading_suspended": bool(kr and kr.krxTradingSuspended),
            "liquidation_trading": bool(kr and kr.liquidationTrading),
            "list_date": detail.listDate,
            "delist_date": detail.delistDate,
            "source": "toss",
        }
    assert item is not None
    return {
        "symbol": item.symbol,
        "name": item.name,
        "market": market,
        "status": "ACTIVE",  # stocks/all 기본 필터가 ACTIVE
        "security_type": item.securityType,
        "is_common_share": item.isCommonShare,
        "trading_suspended": False,
        "liquidation_trading": False,
        "list_date": None,
        "delist_date": None,
        "source": "toss",
    }


async def sync_stocks(
    api: TossApi, session: AsyncSession, *, extra_symbols: Sequence[str] = ()
) -> StockSyncResult:
    """상장 종목을 upsert 하고, 목록에서 빠진 기존 ACTIVE 종목은 상세 조회로 상태를 갱신한다."""
    listed: dict[str, tuple[str, StockListItem]] = {}
    for market in MARKETS:
        for item in await api.get_stocks_all(market):
            listed[item.symbol] = (market, item)

    wanted = sorted(set(listed) | set(extra_symbols))
    details = await _fetch_details(api, wanted)
    rows = []
    for symbol in wanted:
        market, item = listed.get(symbol, (None, None))
        detail = details.get(symbol)
        if item is not None or detail is not None:
            rows.append(_row(item, detail, market))
    await upsert(session, StockRow, rows, keys=["symbol"], update=STOCK_UPDATE_COLUMNS)

    previously_active = (
        await session.scalars(
            select(StockRow.symbol).where(StockRow.status == "ACTIVE", StockRow.source == "toss")
        )
    ).all()
    gone = sorted(set(previously_active) - set(listed) - set(details))
    gone_details = await _fetch_details(api, gone) if gone else {}
    await upsert(
        session,
        StockRow,
        [_row(None, d, None) for d in gone_details.values()],
        keys=["symbol"],
        update=STOCK_UPDATE_COLUMNS,
    )
    unknown = [s for s in gone if s not in gone_details]
    if unknown:
        logger.warning(
            "목록·상세 조회 모두에서 사라진 종목 %d개를 %s 로 표시: %s",
            len(unknown),
            MISSING_STATUS,
            unknown[:20],
        )
        await session.execute(
            update(StockRow).where(StockRow.symbol.in_(unknown)).values(status=MISSING_STATUS)
        )
    await session.commit()
    return StockSyncResult(listed=len(listed), upserted=len(rows), delisted_or_missing=len(gone))
