from datetime import date

from sqlalchemy import func, select

from engine.collect.stocks import MISSING_STATUS, sync_stocks
from engine.db.models import Stock as StockRow
from engine.toss.models import Stock
from tests.collect.fakes import FakeApi


async def _all(session) -> list[tuple]:
    rows = await session.execute(
        select(
            StockRow.symbol,
            StockRow.name,
            StockRow.status,
            StockRow.security_type,
            StockRow.is_common_share,
            StockRow.list_date,
        ).order_by(StockRow.symbol)
    )
    return list(rows)


async def test_sync_stocks_upserts_listed_stocks_with_details(session) -> None:
    result = await sync_stocks(FakeApi(), session)

    assert result.listed == 3
    rows = await _all(session)
    assert [r.symbol for r in rows] == ["000660", "005930", "069500"]
    samsung = next(r for r in rows if r.symbol == "005930")
    assert (samsung.status, samsung.security_type, samsung.is_common_share) == (
        "ACTIVE",
        "STOCK",
        True,
    )
    assert samsung.list_date == date(1975, 6, 11)


async def test_sync_stocks_is_idempotent(session) -> None:
    api = FakeApi()
    await sync_stocks(api, session)
    first = await _all(session)

    await sync_stocks(api, session)

    assert await _all(session) == first
    assert await session.scalar(select(func.count()).select_from(StockRow)) == 3


class DelistingApi(FakeApi):
    """222220 은 목록에서 빠졌지만 상세 조회로 DELISTED 가 확인되는 종목."""

    async def get_stocks(self, symbols):
        found = await super().get_stocks(symbols)
        if "222220" in symbols:
            template = found[0] if found else (await super().get_stocks(["005930"]))[0]
            found.append(
                template.model_copy(
                    update={
                        "symbol": "222220",
                        "name": "폐지주",
                        "status": "DELISTED",
                        "delistDate": date(2026, 9, 1),
                    }
                )
            )
        return found


async def test_stock_missing_from_list_is_updated_from_detail_or_marked_missing(session) -> None:
    session.add_all(
        [
            StockRow(symbol="222220", name="폐지주", status="ACTIVE", source="toss"),
            StockRow(symbol="111110", name="사라진주", status="ACTIVE", source="toss"),
        ]
    )
    await session.commit()

    result = await sync_stocks(DelistingApi(), session)

    assert result.delisted_or_missing == 2
    statuses = dict((await session.execute(select(StockRow.symbol, StockRow.status))).all())
    assert statuses["222220"] == "DELISTED"
    assert statuses["111110"] == MISSING_STATUS
    delisted = await session.get(StockRow, "222220")
    assert delisted.delist_date == date(2026, 9, 1)


async def test_extra_symbols_are_fetched_even_if_not_listed(session) -> None:
    class NoEtfListApi(FakeApi):
        async def get_stocks_all(self, market, **kw):
            items = await super().get_stocks_all(market, **kw)
            return [i for i in items if i.symbol != "069500"]

    await sync_stocks(NoEtfListApi(), session, extra_symbols=["069500"])

    etf = await session.get(StockRow, "069500")
    assert etf is not None and etf.security_type == "ETF"


def test_stock_model_is_reused_from_toss_models() -> None:
    assert Stock.model_fields["koreanMarketDetail"]
