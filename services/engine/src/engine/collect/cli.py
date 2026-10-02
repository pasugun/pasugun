"""수집 배치 CLI.

uv run collect stocks
uv run collect daily --since 2023-01-01
uv run collect backfill --years 3 [--symbols 005930,000660] [--with-investor] [--include-delisted]
uv run collect run-daily [--date 2026-09-30]      # 스케줄러가 도는 작업을 한 번 실행
"""

import argparse
import asyncio
import logging
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from engine.clock import now_kst
from engine.collect.daily import collect_candles_and_investor, run_daily_collection
from engine.collect.delisted import PykrxSource, import_delisted
from engine.collect.runner import SyncResult
from engine.collect.stocks import sync_stocks
from engine.collect.universe import tradable_symbols
from engine.config import Settings, get_settings
from engine.db.session import make_async_engine, make_async_sessionmaker
from engine.notify.base import LogNotifier
from engine.toss.api import TossApi
from engine.toss.factory import create_toss_api


class ProgressPrinter:
    """진행률을 stderr 에 찍는다. 너무 자주 찍지 않도록 0.5초 간격(마지막은 항상)."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.started = time.monotonic()
        self._last = 0.0

    def __call__(self, done: int, total: int, symbol: str, rows: int | None) -> None:
        now = time.monotonic()
        if done != total and now - self._last < 0.5:
            return
        self._last = now
        elapsed = now - self.started
        eta = elapsed / done * (total - done) if done else 0
        status = "실패" if rows is None else f"+{rows}행"
        print(
            f"[{self.label}] {done}/{total} ({done / total:.0%}) {symbol} {status} "
            f"경과 {elapsed:.0f}s 남은 {eta:.0f}s",
            file=sys.stderr,
        )


@dataclass
class Context:
    api: TossApi
    sessions: async_sessionmaker[AsyncSession]
    settings: Settings


@asynccontextmanager
async def open_context() -> AsyncIterator[Context]:
    settings = get_settings()
    engine = make_async_engine()
    api = create_toss_api(settings)
    try:
        yield Context(api, make_async_sessionmaker(engine), settings)
    finally:
        await api.aclose()
        await engine.dispose()


def _symbols(value: str | None) -> list[str] | None:
    return [s.strip() for s in value.split(",") if s.strip()] if value else None


def _print_results(steps: list[SyncResult]) -> int:
    for step in steps:
        print(f"{step.job}: {step.symbols}종목, {step.rows}행 저장, 실패 {len(step.failed)}")
        for symbol, error in list(step.failed.items())[:10]:
            print(f"  - {symbol}: {error}")
        for key, symbols in step.notes.items():
            print(f"  {key}: {', '.join(symbols[:20])}")
    return 0 if all(s.ok for s in steps) else 2


async def cmd_stocks(ctx: Context, args: argparse.Namespace) -> int:
    async with ctx.sessions() as session:
        result = await sync_stocks(
            ctx.api, session, extra_symbols=ctx.settings.collect_always_symbols
        )
    print(
        f"종목 마스터: 상장 {result.listed}, 저장 {result.upserted}, "
        f"상장폐지·누락 {result.delisted_or_missing}"
    )
    return 0


async def _ensure_stocks(ctx: Context, session: AsyncSession) -> None:
    if not await tradable_symbols(session):
        print("종목 마스터가 비어 있어 먼저 동기화합니다", file=sys.stderr)
        await sync_stocks(ctx.api, session, extra_symbols=ctx.settings.collect_always_symbols)


async def cmd_daily(ctx: Context, args: argparse.Namespace) -> int:
    today = now_kst().date()
    since = args.since or today - timedelta(days=ctx.settings.collect_initial_lookback_days)
    async with ctx.sessions() as session:
        await _ensure_stocks(ctx, session)
        universe, steps = await collect_candles_and_investor(
            ctx.api,
            session,
            ctx.settings,
            as_of=today,
            since=since,
            symbols=_symbols(args.symbols),
            with_investor=not args.no_investor,
            extend_history=args.since is not None,
            progress=ProgressPrinter("daily"),
        )
    print(f"대상 종목: {len(universe)}개")
    return _print_results(steps)


async def cmd_backfill(ctx: Context, args: argparse.Namespace) -> int:
    today = now_kst().date()
    since = args.since or today - timedelta(days=round(365.25 * args.years))
    print(f"백필 기간: {since} ~ {today}", file=sys.stderr)
    async with ctx.sessions() as session:
        await _ensure_stocks(ctx, session)
        _, steps = await collect_candles_and_investor(
            ctx.api,
            session,
            ctx.settings,
            as_of=today,
            since=since,
            symbols=_symbols(args.symbols),
            with_investor=args.with_investor,
            extend_history=True,
            progress=ProgressPrinter("backfill"),
        )
        code = _print_results(steps)
        if args.include_delisted:
            source = PykrxSource(
                ctx.settings.krx_id,
                ctx.settings.krx_pw.get_secret_value() if ctx.settings.krx_pw else None,
            )
            rows = await import_delisted(
                source,
                session,
                start=since,
                end=today,
                active_symbols=set(await tradable_symbols(session)),
                with_investor=args.with_investor,
                progress=ProgressPrinter("delisted"),
            )
            print(f"상장폐지 종목 일봉 {rows}행 보충(source=pykrx)")
    return code


async def cmd_run_daily(ctx: Context, args: argparse.Namespace) -> int:
    result = await run_daily_collection(
        ctx.api,
        ctx.sessions,
        ctx.settings,
        LogNotifier(),
        trade_date=args.date or now_kst().date(),
        progress=ProgressPrinter("run-daily"),
    )
    print(f"{result.trade_date}: {result.status}, 대상 종목 {len(result.universe)}개")
    return _print_results(result.steps) if result.steps else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="collect", description="수집 배치")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("stocks", help="종목 마스터 동기화")

    daily = sub.add_parser("daily", help="일봉·수급 동기화(빠진 날짜 보충)")
    daily.add_argument("--since", type=date.fromisoformat, default=None)
    daily.add_argument("--symbols", default=None, help="콤마 구분. 없으면 대상 종목 전체")
    daily.add_argument("--no-investor", action="store_true")

    backfill = sub.add_parser("backfill", help="백테스트용 과거 데이터 적재")
    period = backfill.add_mutually_exclusive_group()
    period.add_argument("--years", type=float, default=3)
    period.add_argument("--since", type=date.fromisoformat, default=None)
    backfill.add_argument("--symbols", default=None)
    backfill.add_argument("--with-investor", action="store_true")
    backfill.add_argument(
        "--include-delisted", action="store_true", help="pykrx 로 상장폐지 종목 보충(KRX 계정 필요)"
    )

    run_daily = sub.add_parser("run-daily", help="장 마감 배치 전체를 한 번 실행")
    run_daily.add_argument("--date", type=date.fromisoformat, default=None)
    return parser


COMMANDS = {
    "stocks": cmd_stocks,
    "daily": cmd_daily,
    "backfill": cmd_backfill,
    "run-daily": cmd_run_daily,
}


async def run(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    async with open_context() as ctx:
        print(f"# TOSS_MODE={ctx.settings.toss_mode}", file=sys.stderr)
        return await COMMANDS[args.command](ctx, args)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.exit(asyncio.run(run(sys.argv[1:])))


if __name__ == "__main__":
    main()
