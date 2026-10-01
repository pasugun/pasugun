"""(선택) 상장폐지 종목 과거 시세 보충 — 생존자 편향 방지용.

토스는 현재 거래 가능한 종목만 주기 때문에, 백테스트 기간에 상장폐지된 종목은
pykrx(KRX 정보데이터시스템)로 가져온다. 섞을 때 규칙:
- 출처는 source='pykrx' 로 남기고, 이미 있는 toss 행은 절대 덮어쓰지 않는다(ON CONFLICT DO NOTHING).
- pykrx 수정주가는 네이버에서 오는데 상장폐지 종목은 없는 경우가 많아,
  KRX 원주가(adjusted=false)를 쓴다.
- 수급의 외국인은 pykrx '외국인합계'(등록+기타) 로, 토스 '등록외국인' 과 기준이 다르다.

pykrx 는 KRX 로그인(KRX_ID/KRX_PW)이 필요하고, 설치는 `uv sync --extra krx`.
TODO(사용자 확인): 실제 KRX 계정으로 동작을 확인하지 않았다. 컬럼 이름은 pykrx 1.2.9 docstring 기준.
"""

import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from engine.collect.db import upsert
from engine.collect.runner import Progress
from engine.db.models import CandleDaily, InvestorTradingDaily, Stock

logger = logging.getLogger(__name__)

SOURCE = "pykrx"
MARKETS = ("KOSPI", "KOSDAQ")


@dataclass(frozen=True)
class DelistedStock:
    symbol: str
    name: str
    market: str
    last_seen: date  # 상장 목록에서 마지막으로 확인된 날(상장폐지일 근사)


class DelistedSource(Protocol):
    def list_delisted(
        self, start: date, end: date, active_symbols: set[str]
    ) -> list[DelistedStock]: ...

    def candles(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]: ...

    def investor(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]: ...


def month_ends(start: date, end: date) -> list[date]:
    days = []
    d = date(start.year, start.month, 1)
    while d <= end:
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        days.append(min(nxt - timedelta(days=1), end))
        d = nxt
    return days


def looks_like_common_share(symbol: str) -> bool:
    """국내 종목코드 관례: 보통주는 끝자리 0, 우선주는 5·7·9·K 등."""
    return symbol.isdigit() and symbol.endswith("0")


class PykrxSource:
    def __init__(self, krx_id: str | None, krx_pw: str | None) -> None:
        if not (krx_id and krx_pw):
            raise RuntimeError("pykrx 사용에는 .env 의 KRX_ID, KRX_PW 가 필요합니다")
        # pykrx 는 import 시점에 환경변수로 로그인하므로 import 전에 넣는다.
        os.environ["KRX_ID"], os.environ["KRX_PW"] = krx_id, krx_pw
        try:
            from pykrx import stock  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("pykrx 가 없습니다. `uv sync --extra krx` 로 설치하세요") from exc
        self._stock = stock

    @staticmethod
    def _ymd(d: date) -> str:
        return d.strftime("%Y%m%d")

    def list_delisted(
        self, start: date, end: date, active_symbols: set[str]
    ) -> list[DelistedStock]:
        last_seen: dict[str, tuple[str, date]] = {}
        for month_end in month_ends(start, end):
            biz = self._stock.get_nearest_business_day_in_a_week(self._ymd(month_end), prev=True)
            biz_date = date(int(biz[:4]), int(biz[4:6]), int(biz[6:8]))
            for market in MARKETS:
                for symbol in self._stock.get_market_ticker_list(biz, market=market):
                    last_seen[symbol] = (market, biz_date)
        return [
            DelistedStock(symbol, self._stock.get_market_ticker_name(symbol), market, seen)
            for symbol, (market, seen) in sorted(last_seen.items())
            if symbol not in active_symbols and looks_like_common_share(symbol)
        ]

    def candles(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
        df = self._stock.get_market_ohlcv_by_date(
            self._ymd(start), self._ymd(end), symbol, adjusted=False
        )
        return [
            {
                "symbol": symbol,
                "date": ts.date(),
                "open": Decimal(int(row["시가"])),
                "high": Decimal(int(row["고가"])),
                "low": Decimal(int(row["저가"])),
                "close": Decimal(int(row["종가"])),
                "volume": int(row["거래량"]),
                "adjusted": False,
                "source": SOURCE,
            }
            for ts, row in df.iterrows()
            if row["거래량"] > 0
        ]

    def investor(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
        df = self._stock.get_market_trading_volume_by_date(
            self._ymd(start), self._ymd(end), symbol, on="순매수"
        )
        return [
            {
                "symbol": symbol,
                "date": ts.date(),
                "foreigner_net": int(row["외국인합계"]),
                "institution_net": int(row["기관합계"]),
                "individual_net": int(row["개인"]),
                "source": SOURCE,
            }
            for ts, row in df.iterrows()
        ]


async def import_delisted(
    source: DelistedSource,
    session: AsyncSession,
    *,
    start: date,
    end: date,
    active_symbols: set[str],
    with_investor: bool,
    progress: Progress | None = None,
    run_sync: Callable[..., Any] = asyncio.to_thread,
) -> int:
    """기간 중 상장폐지된 종목의 종목 정보·일봉(·수급)을 넣는다. 저장한 일봉 행 수를 돌려준다."""
    delisted = await run_sync(source.list_delisted, start, end, active_symbols)
    logger.info("상장폐지 종목 %d개 보충", len(delisted))
    await upsert(
        session,
        Stock,
        [
            {
                "symbol": d.symbol,
                "name": d.name,
                "market": d.market,
                "status": "DELISTED",
                "security_type": "STOCK",
                "is_common_share": True,
                "delist_date": d.last_seen,
                "source": SOURCE,
            }
            for d in delisted
        ],
        keys=["symbol"],  # 토스에 있는 종목 정보는 건드리지 않는다
    )
    total = 0
    for i, d in enumerate(delisted, 1):
        rows = await run_sync(source.candles, d.symbol, start, min(end, d.last_seen))
        await upsert(session, CandleDaily, rows, keys=["symbol", "date"])
        if with_investor:
            inv = await run_sync(source.investor, d.symbol, start, min(end, d.last_seen))
            await upsert(session, InvestorTradingDaily, inv, keys=["symbol", "date"])
        await session.commit()
        total += len(rows)
        if progress is not None:
            progress(i, len(delisted), d.symbol, len(rows))
    return total
