"""수급 초입: 조건을 딱 만족/불만족하는 가짜 데이터."""

from decimal import Decimal

import pytest

from engine.robots.data import SymbolView
from engine.robots.registry import get_strategy
from engine.robots.surge_entry import SurgeEntry
from tests.series_helpers import D, bar, flat_bars, series, trading_days

DAYS = trading_days(21)


def surge_series(*, volume=3000, close=102, flow=(1, 1), prev_high=101, n_days=21):
    """20일간 거래량 1000·고가 101, 마지막 날 거래량 volume·종가 close."""
    days = DAYS[:n_days]
    bars = [bar(d, 100, prev_high, 99, 100, 1000) for d in days[:-1]]
    bars.append(bar(days[-1], 100, max(close, 103), 99, close, volume))
    flows = {days[-1]: flow} if flow is not None else {}
    return series("AAA", bars, flows)


def check(s):
    return SurgeEntry().check_entry(SymbolView(s, len(s) - 1))


def test_signal_when_all_conditions_met_exactly() -> None:
    # 거래량 3000 = 직전 20일 평균 1000 × 3 (경계 포함), 종가 102 > 전일 고가 101
    signal = check(surge_series())

    assert signal is not None
    assert signal.reference_price == D(102)
    assert signal.score == D(3)
    assert signal.reason["conditions"] == {
        "volume_surge": True,
        "breakout": True,
        "foreigner_buy": True,
        "institution_buy": True,
    }
    assert signal.reason["avg_volume_prev"] == "1000"


def test_average_excludes_today() -> None:
    """당일을 평균에 넣으면 (19×1000+3000)/20=1100 이라 3배 미달이 된다. 넣지 않아야 통과."""
    assert check(surge_series(volume=3000)) is not None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"volume": 2999},  # 3배 미만
        {"close": 101},  # 종가 = 전일 고가 (초과 아님)
        {"flow": (0, 5)},  # 외국인 순매수 0
        {"flow": (5, -1)},  # 기관 순매도
        {"flow": None},  # 수급 데이터 없음
    ],
)
def test_no_signal_when_any_condition_fails(kwargs) -> None:
    assert check(surge_series(**kwargs)) is None


def test_needs_21_bars_for_warmup() -> None:
    s = surge_series(n_days=20)
    strategy = SurgeEntry()

    class Data:
        def view(self, symbol, day):
            return s.view(day)

        def symbols(self):
            return ["AAA"]

        def is_excluded(self, symbol, day):
            return False

    assert strategy.check_entry_daily("AAA", DAYS[19], Data()) is None


def test_excluded_symbol_gets_no_signal() -> None:
    s = surge_series()

    class Data:
        def view(self, symbol, day):
            return s.view(day)

        def symbols(self):
            return ["AAA"]

        def is_excluded(self, symbol, day):
            return True  # 매수 유의 종목

    assert SurgeEntry().check_entry_daily("AAA", DAYS[-1], Data()) is None
    assert SurgeEntry().select_candidates(DAYS[-1], Data()) == []


def test_exit_rule_from_entry_price() -> None:
    strategy = SurgeEntry()
    rule = strategy.exit_rule(check(surge_series()), entry_price=Decimal(10_000))
    assert (rule.target_price, rule.stop_price, rule.max_holding_days) == (
        Decimal(10_500),
        Decimal(9_700),
        3,
    )


def test_params_can_be_overridden() -> None:
    strategy = get_strategy("surge_entry", {"volume_multiple": "2"})
    assert strategy.check_entry(SymbolView(surge_series(volume=2000), 20)) is not None


def test_candidates_sorted_by_score() -> None:
    a = surge_series(volume=1600)
    b = series(
        "BBB", [*flat_bars(DAYS[:-1]), bar(DAYS[-1], 100, 100, 100, 100, 2500)], {DAYS[-1]: (1, 1)}
    )

    class Data:
        def view(self, symbol, day):
            return {"AAA": a, "BBB": b}[symbol].view(day)

        def symbols(self):
            return ["AAA", "BBB"]

        def is_excluded(self, symbol, day):
            return False

    picked = SurgeEntry().select_candidates(DAYS[-1], Data())
    assert [c.symbol for c in picked] == ["BBB", "AAA"]
