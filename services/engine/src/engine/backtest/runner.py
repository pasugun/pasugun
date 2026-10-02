"""백테스트 실행: 신호 생성 → 구간별(학습/검증/전체) 시뮬레이션 → 지표·벤치마크·관문.

같은 신호로 비용을 넣은 시뮬레이션(net)과 비용 0 시뮬레이션(gross)을 따로 돌려
비용 차감 전후를 비교한다.
구간마다 초기 자본으로 새로 시작하고, 구간 마지막 날 남은 보유는 종가로 청산한다(END).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from engine.backtest.benchmark import buy_and_hold
from engine.backtest.config import BacktestConfig, CostConfig
from engine.backtest.gate import GateResult, passes_gate
from engine.backtest.metrics import Metrics, compute_metrics
from engine.backtest.portfolio import SimResult, simulate
from engine.backtest.signals import PlannedEntry, next_trading_day_map, signals_for_series
from engine.robots.base import Strategy, jsonable
from engine.robots.data import SymbolSeries


@dataclass(frozen=True)
class Segment:
    name: str  # train | test | full
    start: date
    end: date


@dataclass
class SegmentReport:
    segment: Segment
    market_days: int
    net: Metrics
    gross: Metrics
    benchmark_net: Metrics
    benchmark_gross: Metrics
    gate: GateResult
    sim: SimResult

    @property
    def excess_net(self):
        return self.net.total_return - self.benchmark_net.total_return

    @property
    def excess_gross(self):
        return self.gross.total_return - self.benchmark_gross.total_return

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.segment.start.isoformat(),
            "end": self.segment.end.isoformat(),
            "market_days": self.market_days,
            "net": self.net.to_dict(),
            "gross": self.gross.to_dict(),
            "benchmark_net": self.benchmark_net.to_dict(),
            "benchmark_gross": self.benchmark_gross.to_dict(),
            "excess_net": str(self.excess_net),
            "excess_gross": str(self.excess_gross),
            "gate": self.gate.to_dict(),
            "skipped_entries": dict(self.sim.skipped),
        }


@dataclass
class BacktestReport:
    robot_id: str
    params: dict[str, Any]
    config: BacktestConfig
    start: date
    end: date
    split: date | None
    signal_count: int
    segments: list[SegmentReport] = field(default_factory=list)

    def segment(self, name: str) -> SegmentReport | None:
        return next((s for s in self.segments if s.segment.name == name), None)

    @property
    def gate_segment(self) -> SegmentReport:
        """관문 판정 기준: 검증 구간이 있으면 검증, 없으면 전체."""
        return self.segment("test") or self.segments[-1]

    def metrics_json(self) -> dict[str, Any]:
        return {
            "signal_count": self.signal_count,
            "gate_segment": self.gate_segment.segment.name,
            "gate_passed": self.gate_segment.gate.passed,
            "segments": {s.segment.name: s.to_dict() for s in self.segments},
        }


def build_segments(start: date, end: date, split: date | None) -> list[Segment]:
    if start > end:
        raise ValueError("시작일이 종료일보다 늦습니다")
    if split is None:
        return [Segment("full", start, end)]
    if not start < split <= end:
        raise ValueError("split 은 시작일 이후, 종료일 이하여야 합니다")
    return [
        Segment("train", start, split - timedelta(days=1)),
        Segment("test", split, end),
        Segment("full", start, end),
    ]


@dataclass
class SignalPass:
    """종목별 신호 생성 결과. 신호가 난 종목의 시계열만 시뮬레이션용으로 남긴다."""

    entries: list[PlannedEntry] = field(default_factory=list)
    series: dict[str, SymbolSeries] = field(default_factory=dict)
    symbols_scanned: int = 0

    def add(self, series: SymbolSeries, entries: list[PlannedEntry]) -> None:
        self.symbols_scanned += 1
        if entries:
            self.entries.extend(entries)
            self.series[series.symbol] = series


def scan_series(
    strategy: Strategy,
    series: SymbolSeries,
    *,
    start: date,
    end: date,
    market_days: Sequence[date],
    config: BacktestConfig,
) -> list[PlannedEntry]:
    return signals_for_series(
        strategy,
        series,
        start=start,
        end=end,
        universe=config.universe,
        next_day=next_trading_day_map(market_days),
    )


def _simulate(
    strategy: Strategy,
    entries: Sequence[PlannedEntry],
    series: Mapping[str, SymbolSeries],
    days: Sequence[date],
    costs: CostConfig,
    config: BacktestConfig,
) -> SimResult:
    in_range = [e for e in entries if days[0] <= e.entry_date <= days[-1]]
    return simulate(strategy, in_range, series, days, costs, config.portfolio)


def run_segments(
    strategy: Strategy,
    signal_pass: SignalPass,
    benchmark: SymbolSeries,
    market_days: Sequence[date],
    *,
    start: date,
    end: date,
    split: date | None,
    config: BacktestConfig,
) -> BacktestReport:
    report = BacktestReport(
        robot_id=strategy.id,
        params=jsonable(strategy.params.model_dump()),
        config=config,
        start=start,
        end=end,
        split=split,
        signal_count=len(signal_pass.entries),
    )
    capital = config.portfolio.initial_capital
    for seg in build_segments(start, end, split):
        days = [d for d in market_days if seg.start <= d <= seg.end]
        if len(days) < 2:
            raise ValueError(f"{seg.name} 구간의 거래일이 부족합니다: {seg.start}~{seg.end}")
        net = _simulate(
            strategy, signal_pass.entries, signal_pass.series, days, config.costs, config
        )
        gross = _simulate(
            strategy, signal_pass.entries, signal_pass.series, days, CostConfig.zero(), config
        )
        net_m = compute_metrics(net, days[0])
        bench_net = buy_and_hold(benchmark, days, capital, config.costs)
        report.segments.append(
            SegmentReport(
                segment=seg,
                market_days=len(days),
                net=net_m,
                gross=compute_metrics(gross, days[0]),
                benchmark_net=bench_net,
                benchmark_gross=buy_and_hold(benchmark, days, capital, CostConfig.zero()),
                gate=passes_gate(
                    net_excess_return=net_m.total_return - bench_net.total_return,
                    mdd=net_m.mdd,
                    trade_count=net_m.trade_count,
                    gate=config.gate,
                ),
                sim=net,
            )
        )
    return report


def run_backtest(
    strategy: Strategy,
    all_series: Iterable[SymbolSeries],
    benchmark: SymbolSeries,
    *,
    start: date,
    end: date,
    split: date | None,
    config: BacktestConfig,
) -> BacktestReport:
    """메모리에 있는 시계열로 한 번에 실행(테스트·소규모용). 거래일은 벤치마크 일봉 날짜."""
    market_days = [b.date for b in benchmark.bars]
    signal_pass = SignalPass()
    for series in all_series:
        signal_pass.add(
            series,
            scan_series(
                strategy, series, start=start, end=end, market_days=market_days, config=config
            ),
        )
    return run_segments(
        strategy,
        signal_pass,
        benchmark,
        market_days,
        start=start,
        end=end,
        split=split,
        config=config,
    )
