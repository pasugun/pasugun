from datetime import date
from decimal import Decimal

import pytest

from engine.backtest.benchmark import MissingBenchmarkData, buy_and_hold
from engine.backtest.config import CostConfig, GateConfig
from engine.backtest.gate import passes_gate
from engine.backtest.metrics import cagr, max_drawdown, trade_stats
from engine.backtest.portfolio import Trade
from tests.series_helpers import bar, series, trading_days


def test_max_drawdown() -> None:
    # 고점 120 → 90 = -25%, 고점 130 → 117 = -10% → 최악 -25%
    assert max_drawdown([Decimal(v) for v in (100, 120, 90, 130, 117)]) == Decimal("-0.25")
    assert max_drawdown([Decimal(v) for v in (100, 110, 120)]) == 0


def test_cagr() -> None:
    # 2년(730.5일)에 100 → 121 이면 연 10%
    start, end = date(2024, 1, 1), date(2026, 1, 1)  # 731일
    value = cagr(Decimal(100), Decimal(121), start, end)
    assert value == pytest.approx(Decimal("1.21") ** (Decimal("365.25") / 731) - 1)
    assert round(value, 3) == Decimal("0.100")
    assert cagr(Decimal(100), Decimal(121), start, start) is None


def _trade(net: str, held: int) -> Trade:
    d = date(2026, 1, 5)
    return Trade(
        "A",
        d,
        d,
        Decimal(100),
        1,
        d,
        Decimal(100),
        "X",
        held,
        cost_basis=Decimal(100),
        proceeds=Decimal(100) * (1 + Decimal(net)),
    )


def test_trade_stats() -> None:
    stats = trade_stats([_trade("0.10", 2), _trade("-0.05", 4), _trade("0.02", 3), _trade("0", 1)])
    assert stats["trade_count"] == 4
    assert stats["win_rate"] == Decimal("0.5")  # 0 은 진 거래로 센다
    assert stats["avg_win"] == Decimal("0.06")
    assert stats["avg_loss"] == Decimal("-0.025")
    assert stats["avg_holding_days"] == Decimal("2.5")


def test_trade_stats_empty() -> None:
    assert trade_stats([])["win_rate"] is None


GATE = GateConfig(min_excess_return=Decimal(0), mdd_floor=Decimal("-0.20"), min_trades=100)


@pytest.mark.parametrize(
    ("excess", "mdd", "trades", "passed"),
    [
        ("0.01", "-0.10", 100, True),
        ("0", "-0.10", 100, False),  # 초과수익은 0 초과여야
        ("0.01", "-0.20", 100, False),  # MDD 는 -20% 보다 커야(경계 미통과)
        ("0.01", "-0.10", 99, False),
    ],
)
def test_passes_gate(excess, mdd, trades, passed) -> None:
    result = passes_gate(
        net_excess_return=Decimal(excess), mdd=Decimal(mdd), trade_count=trades, gate=GATE
    )
    assert result.passed is passed


def test_benchmark_buy_and_hold_hand_calculated() -> None:
    days = trading_days(4)
    s = series(
        "069500",
        [
            bar(days[0], 100, 100, 100, 100),
            bar(days[1], 100, 100, 80, 80),
            bar(days[2], 80, 120, 80, 120),
            bar(days[3], 120, 120, 110, 110),
        ],
    )
    m = buy_and_hold(s, days, Decimal(1000), CostConfig.zero())
    # 시가 100 에 10주 → 평가 1000, 800, 1200, 매도 1100
    assert m.total_return == Decimal("0.1")
    assert m.mdd == Decimal("-0.2")
    assert m.final_equity == Decimal(1100)


def test_benchmark_requires_full_coverage() -> None:
    days = trading_days(4)
    s = series("069500", [bar(d, 100, 100, 100, 100) for d in days[1:]])
    with pytest.raises(MissingBenchmarkData):
        buy_and_hold(s, days, Decimal(1000), CostConfig.zero())
