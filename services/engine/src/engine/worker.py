"""engine-worker: 장 마감 수집 배치 스케줄러 (5단계에서 실시간 워커가 추가된다).

    uv run python -m engine.worker

- COLLECT_CRON(기본 평일 15:50 KST)에 run_daily_collection 실행
- 워커가 꺼져 있다가 켜졌을 때 오늘 실행 시각이 지났고 아직 끝난 기록이 없으면 바로 한 번 실행
- 실패는 로그 + 알림(Notifier). 스케줄러는 계속 돈다.
"""

import asyncio
import logging
import signal
from datetime import datetime, time, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from engine.clock import KST, now_kst
from engine.collect.daily import JOB, run_daily_collection
from engine.collect.runs import has_finished_run
from engine.config import Settings, get_settings
from engine.db.session import make_async_engine, make_async_sessionmaker
from engine.notify.base import LogNotifier, Notifier
from engine.toss.api import TossApi
from engine.toss.factory import create_toss_api

logger = logging.getLogger(__name__)


def collect_trigger(settings: Settings) -> CronTrigger:
    return CronTrigger.from_crontab(settings.collect_cron, timezone=KST)


def scheduled_time_today(trigger: CronTrigger, now: datetime) -> datetime | None:
    """오늘 날짜에 잡힌 첫 실행 시각. 오늘 실행이 없으면(주말 등) None."""
    start_of_day = datetime.combine(now.date(), time.min, KST)
    fire = trigger.get_next_fire_time(None, start_of_day - timedelta(microseconds=1))
    return fire if fire is not None and fire.date() == now.date() else None


def catch_up_due(trigger: CronTrigger, now: datetime, already_finished: bool) -> bool:
    scheduled = scheduled_time_today(trigger, now)
    return scheduled is not None and now >= scheduled and not already_finished


async def daily_job(
    api: TossApi,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    notifier: Notifier,
) -> None:
    trade_date = now_kst().date()
    try:
        await run_daily_collection(api, sessions, settings, notifier, trade_date=trade_date)
    except Exception:
        # run_daily_collection 이 이미 기록·알림을 남겼다. 스케줄러가 죽지 않게 삼킨다.
        logger.exception("%s 수집 배치 실패", trade_date)


async def main_async() -> None:
    settings = get_settings()
    engine = make_async_engine()
    sessions = make_async_sessionmaker(engine)
    api = create_toss_api(settings)
    notifier: Notifier = LogNotifier()  # TODO(5단계): 텔레그램 운영 알림으로 교체
    trigger = collect_trigger(settings)

    scheduler = AsyncIOScheduler(timezone=KST)
    scheduler.add_job(
        daily_job,
        trigger,
        args=[api, sessions, settings, notifier],
        id=JOB,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    scheduler.start()
    logger.info(
        "engine-worker 시작 (수집 cron=%r, TOSS_MODE=%s)", settings.collect_cron, settings.toss_mode
    )

    now = now_kst()
    async with sessions() as session:
        finished = await has_finished_run(session, JOB, now.date())
    if catch_up_due(trigger, now, finished):
        logger.info("오늘 수집 시각이 지났는데 기록이 없어 바로 실행합니다")
        await daily_job(api, sessions, settings, notifier)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    scheduler.shutdown(wait=False)
    await api.aclose()
    await engine.dispose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
