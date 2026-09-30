"""토스 API 확인용 CLI (조회 전용).

    uv run python -m engine.toss.cli prices 005930
    uv run python -m engine.toss.cli candles 005930 --count 5
    uv run python -m engine.toss.cli calendar

TOSS_MODE=mock(기본)이면 fixture 로, live 면 실제 API 로 조회한다.
live 는 허용 IP 등록과 DB(토큰 저장)가 필요하다.
"""

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

from pydantic import BaseModel, TypeAdapter

from engine.config import get_settings
from engine.toss.api import TossApi
from engine.toss.errors import TossApiError, TossAuthError
from engine.toss.factory import create_toss_api
from engine.toss.pagination import iter_closed_orders

Command = Callable[[TossApi, argparse.Namespace], Awaitable[Any]]


def _symbols(value: str) -> list[str]:
    return [s.strip() for s in value.split(",") if s.strip()]


async def _orders(api: TossApi, a: argparse.Namespace) -> Any:
    return [o async for o in iter_closed_orders(api, a.from_, a.to, symbol=a.symbol)]


COMMANDS: dict[str, Command] = {
    "accounts": lambda api, a: api.get_accounts(),
    "prices": lambda api, a: api.get_prices(_symbols(a.symbols)),
    "stocks": lambda api, a: api.get_stocks(_symbols(a.symbols)),
    "stocks-all": lambda api, a: api.get_stocks_all(a.market),
    "warnings": lambda api, a: api.get_warnings(a.symbol),
    "candles": lambda api, a: api.get_candles(a.symbol, a.interval, count=a.count),
    "investor": lambda api, a: api.get_investor_trading(a.symbol, count=a.count),
    "calendar": lambda api, a: api.get_market_calendar_kr(a.date),
    "rankings": lambda api, a: api.get_rankings(a.type, "KR", a.duration, count=a.count),
    "holdings": lambda api, a: api.get_holdings(),
    "buying-power": lambda api, a: api.get_buying_power("KRW"),
    "orders": _orders,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="engine.toss.cli", description="토스 API 조회 확인용")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("accounts", help="계좌 목록")
    for name in ("prices", "stocks"):
        sub.add_parser(name, help=f"{name} (콤마로 여러 종목)").add_argument("symbols")
    sub.add_parser("stocks-all", help="마켓별 전체 종목").add_argument(
        "market", choices=["KOSPI", "KOSDAQ", "KR_ETC"]
    )
    sub.add_parser("warnings", help="매수 유의사항").add_argument("symbol")

    candles = sub.add_parser("candles", help="캔들(최신순)")
    candles.add_argument("symbol")
    candles.add_argument("--interval", choices=["1d", "1m"], default="1d")
    candles.add_argument("--count", type=int, default=5)

    investor = sub.add_parser("investor", help="투자자별 매매동향")
    investor.add_argument("symbol")
    investor.add_argument("--count", type=int, default=5)

    sub.add_parser("calendar", help="국내 장 운영 시간").add_argument(
        "--date", type=date.fromisoformat, default=None
    )

    rankings = sub.add_parser("rankings", help="국내 랭킹")
    rankings.add_argument("--type", default="MARKET_TRADING_AMOUNT")
    rankings.add_argument("--duration", default="1d")
    rankings.add_argument("--count", type=int, default=10)

    sub.add_parser("holdings", help="보유 주식")
    sub.add_parser("buying-power", help="매수 가능 금액(KRW)")

    orders = sub.add_parser("orders", help="종료된 주문(체결·취소) 전체")
    orders.add_argument("--from", dest="from_", type=date.fromisoformat, default=None)
    orders.add_argument("--to", type=date.fromisoformat, default=None)
    orders.add_argument("--symbol", default=None)
    return parser


def to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return TypeAdapter(Any).dump_python(value, mode="json")


async def run(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    api = create_toss_api()
    try:
        result = await COMMANDS[args.command](api, args)
    except (TossApiError, TossAuthError) as exc:
        print(f"토스 API 에러: {exc}", file=sys.stderr)
        return 1
    finally:
        await api.aclose()
    print(f"# TOSS_MODE={get_settings().toss_mode}", file=sys.stderr)
    print(json.dumps(to_jsonable(result), ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # httpx 는 INFO 에서 요청 URL 을 찍는다. 쿼리에 비밀값은 없지만 소음을 줄인다.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.exit(asyncio.run(run(sys.argv[1:])))


if __name__ == "__main__":
    main()
