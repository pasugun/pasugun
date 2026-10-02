from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal

from engine.clock import KST
from engine.config import Settings, TossMode, get_settings
from engine.toss.errors import TossApiError
from engine.toss.mock import MockTossClient
from engine.toss.models import Candle, CandlePage


def business_days(end: date, count: int) -> list[date]:
    """end 이하 평일 count 개, 최신순."""
    days: list[date] = []
    d = end
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return days


def make_candles(
    days: list[date], close: Callable[[int], int], volume: int = 1_000_000
) -> list[Candle]:
    """days(최신순)에 대해 i번째(0=최신) 종가가 close(i) 인 봉."""
    return [
        Candle(
            timestamp=datetime.combine(d, datetime.min.time(), KST),
            openPrice=Decimal(close(i)),
            highPrice=Decimal(close(i)) + 100,
            lowPrice=Decimal(close(i)) - 100,
            closePrice=Decimal(close(i)),
            volume=Decimal(volume),
            currency="KRW",
        )
        for i, d in enumerate(days)
    ]


class FakeApi(MockTossClient):
    """fixture 기반 mock + 종목별 캔들을 테스트에서 바꿔 끼울 수 있는 가짜 API."""

    def __init__(self) -> None:
        super().__init__(get_settings().toss_mock_fixtures_dir)
        self.candles: dict[str, list[Candle]] = {}
        self.candle_requests: list[str] = []
        self.calendar_requests: list[date] = []
        self.fail_candles: set[str] = set()

    async def get_candles(self, symbol, interval, *, count=None, before=None, adjusted=None):
        self.candle_requests.append(symbol)
        if symbol in self.fail_candles:
            raise TossApiError(500, "internal-error", "일시 장애", "fake")
        if symbol not in self.candles:
            return await super().get_candles(
                symbol, interval, count=count, before=before, adjusted=adjusted
            )
        eligible = [c for c in self.candles[symbol] if before is None or c.timestamp <= before]
        page = eligible[: count or 100]
        has_more = len(eligible) > len(page)
        return CandlePage(candles=page, nextBefore=page[-1].timestamp if has_more else None)

    async def get_market_calendar_kr(self, day=None):
        self.calendar_requests.append(day)
        return await super().get_market_calendar_kr(day)


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "toss_mode": TossMode.MOCK,
        "database_url": get_settings().database_url,
        "collect_concurrency": 2,
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]
