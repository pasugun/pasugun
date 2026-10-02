from datetime import datetime

import pytest

from engine.clock import KST
from engine.worker import catch_up_due, collect_trigger, scheduled_time_today
from tests.collect.fakes import make_settings

TRIGGER = collect_trigger(make_settings(collect_cron="50 15 * * mon-fri"))


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=KST)


def test_scheduled_time_today_on_weekday_and_weekend() -> None:
    assert scheduled_time_today(TRIGGER, at(30, 9)) == at(30, 15, 50)  # 수요일
    assert scheduled_time_today(TRIGGER, datetime(2026, 10, 3, 12, tzinfo=KST)) is None  # 토요일


@pytest.mark.parametrize(
    ("now", "finished", "expected"),
    [
        (at(30, 16), False, True),  # 실행 시각 지났고 기록 없음 → 바로 실행
        (at(30, 16), True, False),  # 이미 끝남
        (at(30, 15, 49), False, False),  # 아직 실행 시각 전
        (datetime(2026, 10, 3, 18, tzinfo=KST), False, False),  # 주말
    ],
)
def test_catch_up_due(now: datetime, finished: bool, expected: bool) -> None:
    assert catch_up_due(TRIGGER, now, finished) is expected
