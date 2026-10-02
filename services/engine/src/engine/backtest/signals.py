"""종목별로 진입 신호를 만든다(D 장 마감 판단 → D+1 시가 진입 예정)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from engine.backtest.config import UniverseConfig
from engine.indicators import sma
from engine.robots.base import EntrySignal, Strategy
from engine.robots.data import SymbolSeries, SymbolView


@dataclass(frozen=True)
class PlannedEntry:
    signal: EntrySignal
    entry_date: date  # 신호일 다음 거래일

    @property
    def symbol(self) -> str:
        return self.signal.symbol


def next_trading_day_map(market_days: Sequence[date]) -> dict[date, date]:
    return {d: market_days[i + 1] for i, d in enumerate(market_days[:-1])}


def avg_trading_value(series: SymbolSeries, days: int):
    return series.indicator(
        f"trading_value_sma{days}", lambda s: sma(s.field("trading_value"), days)
    )


def signals_for_series(
    strategy: Strategy,
    series: SymbolSeries,
    *,
    start: date,
    end: date,
    universe: UniverseConfig,
    next_day: dict[date, date],
) -> list[PlannedEntry]:
    """[start, end] 의 각 거래일 장 마감에 진입 조건을 본다.

    - 그날까지의 평균 거래대금이 기준 미만이면 대상 아님(시점별 유니버스, 미래 데이터 미사용)
    - 거래량 0(거래정지)인 날은 건너뛴다
    - 다음 거래일 일봉이 없으면(거래정지·상장폐지) 진입하지 않는다
    """
    value_avg = avg_trading_value(series, universe.avg_days)
    out: list[PlannedEntry] = []
    for i, bar in enumerate(series.bars):
        if bar.date < start or bar.date > end:
            continue
        if bar.volume == 0 or i + 1 < strategy.min_bars:
            continue
        avg = value_avg[i]
        if avg is None or avg < universe.min_avg_trading_value:
            continue
        signal = strategy.check_entry(SymbolView(series, i))
        if signal is None:
            continue
        entry_date = next_day.get(bar.date)
        if entry_date is None or entry_date > end or series.index_of(entry_date) is None:
            continue
        out.append(PlannedEntry(signal, entry_date))
    return out
