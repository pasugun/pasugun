from datetime import date

from engine.toss.api import TossApi


async def is_trading_day(api: TossApi, day: date) -> bool:
    """market-calendar/KR 기준으로 day 에 정규장이 열리는지."""
    calendar = await api.get_market_calendar_kr(day)
    return calendar.today.date == day and calendar.today.is_open
