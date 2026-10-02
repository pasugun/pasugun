"""벤치마크: 같은 구간 첫 거래일 시가에 KODEX 200 을 사서 마지막 거래일 종가까지 보유."""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from engine.backtest import costs as cost_calc
from engine.backtest.config import CostConfig
from engine.backtest.metrics import Metrics, cagr, max_drawdown, total_return
from engine.robots.data import SymbolSeries


class MissingBenchmarkData(RuntimeError):
    pass


def buy_and_hold(
    series: SymbolSeries, market_days: Sequence[date], capital: Decimal, costs: CostConfig
) -> Metrics:
    bars = [series.bars[i] for d in market_days if (i := series.index_of(d)) is not None]
    if not bars or bars[0].date != market_days[0] or bars[-1].date != market_days[-1]:
        raise MissingBenchmarkData(
            f"벤치마크 {series.symbol} 일봉이 구간 "
            f"{market_days[0]}~{market_days[-1]} 을 덮지 않습니다"
        )
    etf_costs = costs.without_tax()  # ETF 매도 거래세 없음
    qty = cost_calc.max_qty(capital, bars[0].open, etf_costs)
    b = cost_calc.buy(qty, bars[0].open, etf_costs)
    cash = capital - b.total
    values = [capital, *(cash + qty * bar.close for bar in bars[:-1])]
    final = cash + cost_calc.sell(qty, bars[-1].close, etf_costs).net
    values.append(final)
    return Metrics(
        total_return=total_return(capital, final),
        cagr=cagr(capital, final, bars[0].date, bars[-1].date),
        mdd=max_drawdown(values),
        trade_count=1,
        win_rate=None,
        avg_win=None,
        avg_loss=None,
        avg_holding_days=Decimal(len(bars)),
        final_equity=final,
    )
