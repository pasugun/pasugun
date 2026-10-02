"""성과 지표."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal

from engine.backtest.portfolio import SimResult, Trade

DAYS_PER_YEAR = Decimal("365.25")


@dataclass(frozen=True)
class Metrics:
    total_return: Decimal
    cagr: Decimal | None
    mdd: Decimal  # 0 이하. -0.2 = -20%
    trade_count: int
    win_rate: Decimal | None
    avg_win: Decimal | None  # 이긴 거래의 평균 수익률
    avg_loss: Decimal | None  # 진(0 이하) 거래의 평균 수익률
    avg_holding_days: Decimal | None
    final_equity: Decimal

    def to_dict(self) -> dict[str, str | int | None]:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in asdict(self).items()}


def total_return(initial: Decimal, final: Decimal) -> Decimal:
    return final / initial - 1


def cagr(initial: Decimal, final: Decimal, start: date, end: date) -> Decimal | None:
    """연복리 수익률. 기간은 달력 일수/365.25."""
    days = (end - start).days
    if days <= 0 or initial <= 0 or final <= 0:
        return None
    return (final / initial) ** (DAYS_PER_YEAR / days) - 1


def max_drawdown(values: Sequence[Decimal]) -> Decimal:
    peak: Decimal | None = None
    worst = Decimal(0)
    for v in values:
        peak = v if peak is None or v > peak else peak
        worst = min(worst, v / peak - 1)
    return worst


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    return sum(values, Decimal(0)) / len(values) if values else None


def trade_stats(trades: Sequence[Trade]) -> dict[str, Decimal | int | None]:
    returns = [t.net_return for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    return {
        "trade_count": len(trades),
        "win_rate": Decimal(len(wins)) / len(trades) if trades else None,
        "avg_win": _mean(wins),
        "avg_loss": _mean(losses),
        "avg_holding_days": _mean([Decimal(t.held_days) for t in trades]),
    }


def compute_metrics(result: SimResult, start: date) -> Metrics:
    """start 는 구간 첫 거래일. 초기 자본을 그날 시가 직전 평가액으로 본다."""
    values = [result.initial_capital, *(v for _, v in result.equity_curve)]
    final = values[-1]
    end = result.equity_curve[-1][0] if result.equity_curve else start
    return Metrics(
        total_return=total_return(result.initial_capital, final),
        cagr=cagr(result.initial_capital, final, start, end),
        mdd=max_drawdown(values),
        final_equity=final,
        **trade_stats(result.trades),  # type: ignore[arg-type]
    )
