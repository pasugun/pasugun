from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from engine.collect.candles import sync_daily_candles
from engine.db.models import CandleDaily
from tests.collect.fakes import FakeApi, business_days, make_candles

END = date(2026, 9, 29)
DAYS = business_days(END, 30)  # 최신순
SINCE = DAYS[-1]


async def _rows(session, symbol: str = "AAA") -> dict[date, Decimal]:
    rows = await session.execute(
        select(CandleDaily.date, CandleDaily.close).where(CandleDaily.symbol == symbol)
    )
    return dict(rows.all())


async def _sync(api, session, symbols=("AAA",), since=SINCE, extend_history=False):
    return await sync_daily_candles(
        api,
        session,
        list(symbols),
        since=since,
        refetch_recent_days=5,
        extend_history=extend_history,
        concurrency=2,
    )


async def test_first_sync_stores_every_candle_since(session) -> None:
    api = FakeApi()
    api.candles["AAA"] = make_candles(DAYS, lambda i: 10_000 + i)

    result = await _sync(api, session)

    rows = await _rows(session)
    assert result.rows == 30 and len(rows) == 30
    assert rows[END] == Decimal(10_000)
    assert rows[SINCE] == Decimal(10_029)


async def test_resync_is_idempotent_and_refetches_only_recent_5_days(session) -> None:
    api = FakeApi()
    api.candles["AAA"] = make_candles(DAYS, lambda i: 10_000 + i)
    await _sync(api, session)
    before = await _rows(session)

    result = await _sync(api, session)

    assert result.rows == 5  # 최근 5거래일만 다시 받았다
    assert await _rows(session) == before
    count = await session.scalar(select(func.count()).select_from(CandleDaily))
    assert count == 30


async def test_missing_days_are_filled_on_next_run(session) -> None:
    """어제 배치가 실패해 10일치가 비어 있어도 다음 실행에서 채워진다."""
    api = FakeApi()
    api.candles["AAA"] = make_candles(DAYS[10:], lambda i: 20_000 + i)  # 최근 10일 없음
    await _sync(api, session)
    assert max(await _rows(session)) == DAYS[10]

    api.candles["AAA"] = make_candles(DAYS, lambda i: 20_000 + i - 10)
    result = await _sync(api, session)

    rows = await _rows(session)
    assert len(rows) == 30
    assert set(DAYS[:10]) <= set(rows)
    assert result.rows == 15  # 저장돼 있던 최근 5일 + 새 10일


async def test_recent_value_change_updates_rows(session) -> None:
    """최근 5일 안에서 값이 바뀌면(통합 시세 거래량 확정 등) 덮어쓴다."""
    api = FakeApi()
    api.candles["AAA"] = make_candles(DAYS, lambda i: 10_000)
    await _sync(api, session)

    api.candles["AAA"] = make_candles(DAYS, lambda i: 10_000, volume=2_000_000)
    await _sync(api, session)

    latest = await session.get(CandleDaily, {"symbol": "AAA", "date": END})
    assert latest.volume == 2_000_000


async def test_split_changes_whole_adjusted_history_and_triggers_full_refetch(session) -> None:
    api = FakeApi()
    api.candles["AAA"] = make_candles(DAYS, lambda i: 50_000)
    await _sync(api, session)

    # 1:2 액면분할 → 수정주가 기준 과거 전체가 절반
    api.candles["AAA"] = make_candles(DAYS, lambda i: 25_000)
    result = await _sync(api, session)

    assert result.notes["adjusted_refetch"] == ["AAA"]
    assert set((await _rows(session)).values()) == {Decimal(25_000)}


async def test_backfill_since_before_stored_range_fetches_older_days(session) -> None:
    api = FakeApi()
    api.candles["AAA"] = make_candles(DAYS, lambda i: 10_000)
    await _sync(api, session, since=DAYS[9])
    assert len(await _rows(session)) == 10

    await _sync(api, session, since=SINCE, extend_history=True)

    assert len(await _rows(session)) == 30


async def test_daily_mode_does_not_refetch_whole_history_when_api_history_is_short(
    session,
) -> None:
    """신규 상장처럼 API 기간이 since 보다 짧아도 매일 전체를 다시 받지 않는다."""
    api = FakeApi()
    api.candles["AAA"] = make_candles(DAYS[:10], lambda i: 10_000)
    early = date(2025, 1, 1)
    await _sync(api, session, since=early)

    result = await _sync(api, session, since=early)

    assert result.rows == 5


async def test_one_symbol_failure_does_not_stop_others(session) -> None:
    api = FakeApi()
    api.candles["AAA"] = make_candles(DAYS, lambda i: 10_000)
    api.candles["BBB"] = make_candles(DAYS, lambda i: 10_000)
    api.fail_candles.add("BBB")

    result = await _sync(api, session, symbols=("AAA", "BBB"))

    assert list(result.failed) == ["BBB"]
    assert len(await _rows(session, "AAA")) == 30
    assert await _rows(session, "BBB") == {}
