"""과대낙폭 반등.

기본 시계열: 45일 종가 100(고가 101) → 14일간 2씩 하락(98…72) → 60 급락 → 64 반등.
손계산 요약
- 60일 최고 고가 101, 마지막 종가 64 → 낙폭 64/101-1 = -36.6% (≤ -25%)
- 전일까지 계속 하락만 했으므로 전일 RSI = 0 (≤ 30), 당일 상승으로 RSI > 0
- 전일 종가 60 < 전일 볼린저 하단(≈64.98), 당일 종가 64 ≥ 당일 하단(≈61.76)
- 손절 기준 = 최근 20일 최저 저가 = 60 - 1 = 59
"""

from decimal import Decimal

from engine.robots.base import OpenPosition
from engine.robots.data import SymbolView
from engine.robots.oversold_rebound import OversoldRebound
from tests.series_helpers import bar, series, trading_days

BASE = [100] * 45 + [100 - 2 * k for k in range(1, 15)] + [60, 64]


def make(closes):
    days = trading_days(len(closes))
    return series("XXX", [bar(d, c, c + 1, c - 1, c) for d, c in zip(days, closes, strict=True)])


def check(closes):
    s = make(closes)
    return OversoldRebound().check_entry(SymbolView(s, len(s) - 1))


def test_signal_with_all_conditions() -> None:
    signal = check(BASE)

    assert signal is not None
    assert all(signal.reason["conditions"].values())
    assert signal.reason["rsi_prev"] == "0"
    assert signal.reason["high_max"] == "101"
    assert signal.levels["stop"] == Decimal(59)


def test_no_reclaim_when_close_stays_below_lower_band() -> None:
    assert check([*BASE[:-1], 61]) is None  # 61 < 당일 하단 ≈ 61.76


def test_no_signal_without_deep_drawdown() -> None:
    # 같은 모양이지만 전체를 위로 올려 낙폭을 줄인다: 최고 101+60=161, 종가 124 → -23%
    assert check([c + 60 for c in BASE]) is None


def test_no_signal_when_rsi_not_rising() -> None:
    assert check([*BASE[:-1], 60]) is None


def test_needs_60_bars() -> None:
    assert OversoldRebound().min_bars == 60
    assert check(BASE[-59:]) is None


def test_exit_rule_uses_20_day_low_and_no_target() -> None:
    strategy = OversoldRebound()
    rule = strategy.exit_rule(check(BASE), entry_price=Decimal(65))
    assert (rule.target_price, rule.stop_price, rule.max_holding_days) == (None, Decimal(59), 20)


def test_rsi_exit_at_60() -> None:
    strategy = OversoldRebound()
    rising = make([100 + k for k in range(30)])  # 계속 상승 → RSI 100
    falling = make([100 - k for k in range(30)])
    position = OpenPosition("XXX", rising.bars[0].date, Decimal(100), 5, check(BASE))

    assert strategy.check_exit_daily(SymbolView(rising, 29), position) == "RSI_TAKE_PROFIT"
    assert strategy.check_exit_daily(SymbolView(falling, 29), position) is None
