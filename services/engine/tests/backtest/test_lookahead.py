"""미래 데이터 미참조 보장."""

from decimal import Decimal

import pytest

from engine.backtest.config import UniverseConfig
from engine.backtest.signals import next_trading_day_map, signals_for_series
from engine.robots.data import Bar, SymbolView
from engine.robots.oversold_rebound import OversoldRebound
from engine.robots.surge_entry import SurgeEntry
from tests.backtest.scripted import Scripted
from tests.series_helpers import bar, series, trading_days

UNIVERSE = UniverseConfig(min_avg_trading_value=Decimal(0), avg_days=20)


def noisy_bars(n: int, seed: int = 7) -> list[Bar]:
    days = trading_days(n)
    out = []
    price = 10_000
    for i, d in enumerate(days):
        step = ((i * 37 + seed) % 23) - 11
        price = max(1_000, price + step * 60)
        vol = 1_000 + ((i * 53 + seed) % 17) * 400
        out.append(bar(d, price - 50, price + 120, price - 140, price, vol))
    return out


def perturb_after(bars: list[Bar], k: int) -> list[Bar]:
    """k 번째 이후 봉을 완전히 다른 값으로 바꾼다."""
    out = list(bars[: k + 1])
    for b in bars[k + 1 :]:
        out.append(Bar(b.date, b.open * 3, b.high * 3, b.low / 3, b.close / 2, b.volume * 50))
    return out


def all_signals(strategy, bars, flows=None):
    s = series("AAA", bars, flows)
    days = [b.date for b in bars]
    return signals_for_series(
        strategy,
        s,
        start=days[0],
        end=days[-1],
        universe=UNIVERSE,
        next_day=next_trading_day_map(days),
    )


@pytest.mark.parametrize("strategy", [SurgeEntry(), OversoldRebound()])
def test_signals_up_to_d_do_not_change_when_future_changes(strategy) -> None:
    bars = noisy_bars(200)
    flows = {b.date: (1, 1) for b in bars}
    cut = 120
    original = [
        e.signal for e in all_signals(strategy, bars, flows) if e.signal.date <= bars[cut].date
    ]
    perturbed_bars = perturb_after(bars, cut)
    perturbed = [
        e.signal
        for e in all_signals(strategy, perturbed_bars, {b.date: (1, 1) for b in perturbed_bars})
        if e.signal.date <= bars[cut].date
    ]
    assert original == perturbed


def test_indicator_values_at_t_unchanged_by_future_data() -> None:
    bars = noisy_bars(150)
    a, b = series("AAA", bars), series("AAA", perturb_after(bars, 100))
    strategy = OversoldRebound()
    for t in (60, 80, 100):
        va, vb = SymbolView(a, t), SymbolView(b, t)
        assert strategy._rsi(va) == strategy._rsi(vb)
        assert strategy._bb_lower(va) == strategy._bb_lower(vb)
        assert strategy._high_max(va) == strategy._high_max(vb)


def test_view_cannot_look_forward() -> None:
    view = SymbolView(series("AAA", noisy_bars(10)), 5)
    with pytest.raises(ValueError):
        view.bar(-1)
    assert len(view.window("close", 100)) == 6


def test_entry_is_always_next_trading_day() -> None:
    bars = noisy_bars(30)
    days = [b.date for b in bars]
    strategy = Scripted({"AAA": {days[5], days[10], days[29]}})
    entries = signals_for_series(
        strategy,
        series("AAA", bars),
        start=days[0],
        end=days[-1],
        universe=UniverseConfig(min_avg_trading_value=Decimal(0), avg_days=1),
        next_day=next_trading_day_map(days),
    )
    assert [(e.signal.date, e.entry_date) for e in entries] == [
        (days[5], days[6]),
        (days[10], days[11]),
    ]  # 마지막 날 신호는 다음 거래일이 없어 진입하지 않는다


def test_no_entry_when_next_day_is_suspended() -> None:
    bars = noisy_bars(30)
    days = [b.date for b in bars]
    strategy = Scripted({"AAA": {days[5]}})
    s = series("AAA", [b for b in bars if b.date != days[6]])
    entries = signals_for_series(
        strategy,
        s,
        start=days[0],
        end=days[-1],
        universe=UniverseConfig(min_avg_trading_value=Decimal(0), avg_days=1),
        next_day=next_trading_day_map(days),
    )
    assert entries == []


def test_universe_filter_uses_only_data_up_to_signal_day() -> None:
    """거래대금이 나중에 커지는 종목은 그 이전 날짜엔 대상이 아니다."""
    days = trading_days(40)
    bars = [bar(d, 100, 100, 100, 100, 10 if i < 30 else 1_000_000) for i, d in enumerate(days)]
    strategy = Scripted({"AAA": {days[25], days[35]}})
    universe = UniverseConfig(min_avg_trading_value=Decimal(1_000_000), avg_days=5)
    entries = signals_for_series(
        strategy,
        series("AAA", bars),
        start=days[0],
        end=days[-1],
        universe=universe,
        next_day=next_trading_day_map(days),
    )
    assert [e.signal.date for e in entries] == [days[35]]
