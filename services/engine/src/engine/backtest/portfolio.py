"""포트폴리오 시뮬레이션.

하루 처리 순서(거래일 d):
1. 시가: 전날 장 마감에 정한 지표 청산(pending) 실행
2. 시가: 그날 진입 예정 신호를 점수 순으로 매수
   - 이미 보유 중이면 건너뜀, 동시 보유 상한·현금 부족이면 건너뜀
   - 매수 예산 = min(전일 종가 기준 평가액 × 종목당 최대 비중, 현금)
3. 장중: 손절가(저가 ≤ 손절가) → 목표가(고가 ≥ 목표가) 순으로 판정. 둘 다면 손절.
   진입 당일도 시가 이후 고가·저가로 판정한다.
4. 종가: 보유 N거래일째면 종가 청산. 마지막 날이면 전부 종가 청산(END).
5. 장 마감: 지표 청산 조건을 보고 다음 날 시가 청산을 예약. 평가액 기록.
거래정지(그날 일봉 없음)인 종목은 그날 아무것도 하지 않는다.
"""

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from engine.backtest import costs as cost_calc
from engine.backtest.config import CostConfig, PortfolioConfig
from engine.backtest.signals import PlannedEntry
from engine.robots.base import EntrySignal, ExitRule, OpenPosition, Strategy
from engine.robots.data import Bar, SymbolSeries


@dataclass(frozen=True)
class Trade:
    symbol: str
    signal_date: date
    entry_date: date
    entry_price: Decimal  # 원 시가(슬리피지 전)
    qty: int
    exit_date: date
    exit_price: Decimal  # 원 청산 기준가(슬리피지 전)
    exit_reason: str  # TAKE_PROFIT | STOP_LOSS | EXPIRED | RSI_TAKE_PROFIT ... | END
    held_days: int
    cost_basis: Decimal  # 매수 금액 + 수수료
    proceeds: Decimal  # 매도 금액 - 수수료 - 세금

    @property
    def gross_return(self) -> Decimal:
        """비용·슬리피지 전 수익률."""
        return self.exit_price / self.entry_price - 1

    @property
    def net_return(self) -> Decimal:
        return self.proceeds / self.cost_basis - 1

    @property
    def pnl(self) -> Decimal:
        return self.proceeds - self.cost_basis


@dataclass
class _Position:
    signal: EntrySignal
    entry_date: date
    entry_day_index: int
    entry_price: Decimal
    rule: ExitRule
    buy: cost_calc.Buy
    last_close: Decimal
    pending_exit: str | None = None

    @property
    def symbol(self) -> str:
        return self.signal.symbol


@dataclass
class SimResult:
    trades: list[Trade]
    equity_curve: list[tuple[date, Decimal]]
    initial_capital: Decimal
    skipped: Counter[str] = field(default_factory=Counter)


def intraday_exit(bar: Bar, rule: ExitRule, held_days: int) -> tuple[Decimal, str] | None:
    """그날 일봉으로 청산 여부와 기준가를 정한다(보수적 가정은 모듈 docstring 참고)."""
    if rule.stop_price is not None and bar.low <= rule.stop_price:
        return min(rule.stop_price, bar.open), "STOP_LOSS"
    if rule.target_price is not None and bar.high >= rule.target_price:
        return rule.target_price, "TAKE_PROFIT"
    if held_days >= rule.max_holding_days:
        return bar.close, "EXPIRED"
    return None


def simulate(
    strategy: Strategy,
    entries: Sequence[PlannedEntry],
    series_by_symbol: Mapping[str, SymbolSeries],
    market_days: Sequence[date],
    costs: CostConfig,
    portfolio: PortfolioConfig,
) -> SimResult:
    cash = portfolio.initial_capital
    positions: dict[str, _Position] = {}
    trades: list[Trade] = []
    curve: list[tuple[date, Decimal]] = []
    skipped: Counter[str] = Counter()
    equity_prev = cash

    by_day: dict[date, list[PlannedEntry]] = defaultdict(list)
    for e in entries:
        by_day[e.entry_date].append(e)

    def bar_of(symbol: str, day: date) -> Bar | None:
        series = series_by_symbol[symbol]
        i = series.index_of(day)
        return series.bars[i] if i is not None else None

    def close(pos: _Position, day: date, day_index: int, raw_price: Decimal, reason: str) -> None:
        nonlocal cash
        s = cost_calc.sell(pos.buy.qty, raw_price, costs)
        cash += s.net
        trades.append(
            Trade(
                symbol=pos.symbol,
                signal_date=pos.signal.date,
                entry_date=pos.entry_date,
                entry_price=pos.entry_price,
                qty=pos.buy.qty,
                exit_date=day,
                exit_price=raw_price,
                exit_reason=reason,
                held_days=day_index - pos.entry_day_index + 1,
                cost_basis=pos.buy.total,
                proceeds=s.net,
            )
        )
        del positions[pos.symbol]

    last = len(market_days) - 1
    for di, day in enumerate(market_days):
        # 1. 예약된 지표 청산(시가)
        for pos in list(positions.values()):
            bar = bar_of(pos.symbol, day)
            if bar is not None and pos.pending_exit:
                close(pos, day, di, bar.open, pos.pending_exit)

        # 2. 신규 진입(시가). 구간 마지막 날 진입은 그날 종가 강제 청산뿐이라 하지 않는다.
        for e in sorted(by_day.get(day, []), key=lambda e: (-e.signal.score, e.symbol)):
            if di == last:
                skipped["segment_end"] += 1
                continue
            if e.symbol in positions:
                skipped["already_holding"] += 1
                continue
            if len(positions) >= portfolio.max_positions:
                skipped["max_positions"] += 1
                continue
            bar = bar_of(e.symbol, day)
            if bar is None:
                skipped["no_bar"] += 1
                continue
            budget = min(equity_prev * portfolio.max_weight, cash)
            qty = cost_calc.max_qty(budget, bar.open, costs)
            if qty <= 0:
                skipped["no_cash"] += 1
                continue
            b = cost_calc.buy(qty, bar.open, costs)
            cash -= b.total
            positions[e.symbol] = _Position(
                signal=e.signal,
                entry_date=day,
                entry_day_index=di,
                entry_price=bar.open,
                rule=strategy.exit_rule(e.signal, bar.open),
                buy=b,
                last_close=bar.open,
            )

        # 3·4. 장중 손절·익절, 종가 기간 청산, 마지막 날 전부 청산
        for pos in list(positions.values()):
            bar = bar_of(pos.symbol, day)
            if bar is None:
                if di == last:
                    close(pos, day, di, pos.last_close, "END")
                continue
            decision = intraday_exit(bar, pos.rule, di - pos.entry_day_index + 1)
            if decision is not None:
                close(pos, day, di, *decision)
            elif di == last:
                close(pos, day, di, bar.close, "END")
            else:
                pos.last_close = bar.close

        # 5. 장 마감: 지표 청산 예약, 평가액
        for pos in positions.values():
            view = series_by_symbol[pos.symbol].view(day)
            if view is None:
                continue
            held = di - pos.entry_day_index + 1
            pos.pending_exit = strategy.check_exit_daily(
                view, OpenPosition(pos.symbol, pos.entry_date, pos.entry_price, held, pos.signal)
            )
        equity_prev = cash + sum((p.last_close * p.buy.qty for p in positions.values()), Decimal(0))
        curve.append((day, equity_prev))

    return SimResult(trades, curve, portfolio.initial_capital, skipped)
