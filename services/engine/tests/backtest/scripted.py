"""정해 둔 날짜에만 신호를 내는 테스트용 전략."""

from datetime import date
from decimal import Decimal

from engine.robots.base import EntrySignal, ExitRule, OpenPosition, Strategy, StrategyParams
from engine.robots.data import SymbolView


class ScriptedParams(StrategyParams):
    target_pct: Decimal | None = Decimal("0.10")
    stop_pct: Decimal | None = Decimal("0.05")
    max_holding_days: int = 5


class Scripted(Strategy[ScriptedParams]):
    id = "surge_entry"  # backtest_runs FK 를 만족시키려고 시드된 id 를 빌린다
    name = "scripted"
    params_model = ScriptedParams

    def __init__(
        self,
        signal_days: dict[str, set[date]],
        params: ScriptedParams | None = None,
        *,
        scores: dict[str, Decimal] | None = None,
        exit_days: dict[str, set[date]] | None = None,
    ) -> None:
        super().__init__(params)
        self.signal_days = signal_days
        self.scores = scores or {}
        self.exit_days = exit_days or {}
        self.seen_dates: list[date] = []

    def check_entry(self, view: SymbolView) -> EntrySignal | None:
        self.seen_dates.append(view.date)
        if view.date not in self.signal_days.get(view.symbol, set()):
            return None
        return EntrySignal(
            symbol=view.symbol,
            date=view.date,
            reference_price=view.bar().close,
            score=self.scores.get(view.symbol, Decimal(1)),
            reason={},
        )

    def exit_rule(self, signal: EntrySignal, entry_price: Decimal) -> ExitRule:
        p = self.params
        return ExitRule(
            target_price=entry_price * (1 + p.target_pct) if p.target_pct is not None else None,
            stop_price=entry_price * (1 - p.stop_pct) if p.stop_pct is not None else None,
            max_holding_days=p.max_holding_days,
        )

    def candidate_score(self, view):
        return None

    def check_exit_daily(self, view: SymbolView, position: OpenPosition) -> str | None:
        return "INDICATOR" if view.date in self.exit_days.get(view.symbol, set()) else None
