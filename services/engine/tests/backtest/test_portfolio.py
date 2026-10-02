"""포트폴리오 시뮬레이션: 체결 시점, 보수적 청산 판정, 보유 상한."""

from decimal import Decimal

import pytest

from engine.backtest.config import CostConfig, PortfolioConfig
from engine.backtest.portfolio import simulate
from engine.backtest.signals import PlannedEntry
from engine.robots.base import EntrySignal
from tests.backtest.scripted import Scripted, ScriptedParams
from tests.series_helpers import bar, flat_bars, series, trading_days

DAYS = trading_days(10)
ZERO = CostConfig.zero()
PORTFOLIO = PortfolioConfig(
    initial_capital=Decimal(10_000_000), max_positions=10, max_weight=Decimal("0.10")
)


def planned(symbol: str, signal_day_index: int, score: Decimal = Decimal(1)) -> PlannedEntry:
    signal = EntrySignal(symbol, DAYS[signal_day_index], Decimal(100), score, {})
    return PlannedEntry(signal, DAYS[signal_day_index + 1])


def run(bars_by_symbol, entries, *, params=None, costs=ZERO, portfolio=PORTFOLIO, exit_days=None):
    strategy = Scripted({}, params or ScriptedParams(), exit_days=exit_days)
    data = {s: series(s, b) for s, b in bars_by_symbol.items()}
    return simulate(strategy, entries, data, DAYS, costs, portfolio)


def with_day(bars, index, new_bar):
    out = list(bars)
    out[index] = new_bar
    return out


def test_entry_at_next_day_open_not_signal_day_close() -> None:
    bars = with_day(flat_bars(DAYS), 3, bar(DAYS[3], 101, 102, 100, 101))
    result = run({"A": bars}, [planned("A", 2)])

    t = result.trades[0]
    assert t.signal_date == DAYS[2]
    assert t.entry_date == DAYS[3]
    assert t.entry_price == Decimal(101)  # D+1 시가


def test_same_day_target_and_stop_counts_as_stop() -> None:
    # 진입 100, 목표 110, 손절 95. 다음 날 고가 112·저가 94 → 손절 95
    bars = with_day(flat_bars(DAYS), 4, bar(DAYS[4], 100, 112, 94, 105))
    t = run({"A": bars}, [planned("A", 2)]).trades[0]
    assert (t.exit_date, t.exit_reason, t.exit_price) == (DAYS[4], "STOP_LOSS", Decimal(95))


def test_gap_down_below_stop_fills_at_open() -> None:
    bars = with_day(flat_bars(DAYS), 4, bar(DAYS[4], 90, 92, 88, 91))
    t = run({"A": bars}, [planned("A", 2)]).trades[0]
    assert (t.exit_reason, t.exit_price) == ("STOP_LOSS", Decimal(90))


def test_gap_up_above_target_fills_at_target_conservatively() -> None:
    bars = with_day(flat_bars(DAYS), 4, bar(DAYS[4], 115, 118, 114, 116))
    t = run({"A": bars}, [planned("A", 2)]).trades[0]
    assert (t.exit_reason, t.exit_price) == ("TAKE_PROFIT", Decimal(110))


def test_entry_day_intraday_stop_is_checked() -> None:
    bars = with_day(flat_bars(DAYS), 3, bar(DAYS[3], 100, 101, 94, 96))
    t = run({"A": bars}, [planned("A", 2)]).trades[0]
    assert (t.entry_date, t.exit_date, t.exit_reason, t.held_days) == (
        DAYS[3],
        DAYS[3],
        "STOP_LOSS",
        1,
    )


def test_time_exit_at_close_of_nth_day_counting_entry_day_as_day_1() -> None:
    bars = with_day(flat_bars(DAYS), 5, bar(DAYS[5], 100, 101, 99, 103))
    t = run({"A": bars}, [planned("A", 2)], params=ScriptedParams(max_holding_days=3)).trades[0]
    # 진입 DAYS[3] = 1일째, DAYS[5] = 3일째 종가
    assert (t.exit_date, t.exit_reason, t.exit_price, t.held_days) == (
        DAYS[5],
        "EXPIRED",
        Decimal(103),
        3,
    )


def test_indicator_exit_executes_next_day_open() -> None:
    bars = with_day(flat_bars(DAYS), 6, bar(DAYS[6], 104, 105, 103, 104))
    t = run({"A": bars}, [planned("A", 2)], exit_days={"A": {DAYS[5]}}).trades[0]
    assert (t.exit_date, t.exit_reason, t.exit_price) == (DAYS[6], "INDICATOR", Decimal(104))


def test_suspended_day_is_skipped_and_time_exit_waits_for_next_bar() -> None:
    bars = [b for b in flat_bars(DAYS) if b.date != DAYS[5]]  # DAYS[5] 거래정지
    t = run({"A": bars}, [planned("A", 2)], params=ScriptedParams(max_holding_days=3)).trades[0]
    assert (t.exit_date, t.exit_reason, t.held_days) == (DAYS[6], "EXPIRED", 4)


def test_open_positions_are_closed_on_last_day() -> None:
    t = run(
        {"A": flat_bars(DAYS)}, [planned("A", 7)], params=ScriptedParams(max_holding_days=50)
    ).trades[0]
    assert (t.exit_date, t.exit_reason) == (DAYS[-1], "END")


def test_no_entry_on_segment_last_day() -> None:
    result = run({"A": flat_bars(DAYS)}, [planned("A", 8)])
    assert result.trades == []
    assert result.skipped["segment_end"] == 1


def test_max_positions_takes_higher_score_first() -> None:
    portfolio = PORTFOLIO.model_copy(update={"max_positions": 1})
    result = run(
        {"A": flat_bars(DAYS), "B": flat_bars(DAYS)},
        [planned("A", 2, Decimal(1)), planned("B", 2, Decimal(5))],
        portfolio=portfolio,
        params=ScriptedParams(max_holding_days=2),
    )
    assert [t.symbol for t in result.trades] == ["B"]
    assert result.skipped["max_positions"] == 1


def test_signal_while_holding_same_symbol_is_skipped() -> None:
    result = run(
        {"A": flat_bars(DAYS)},
        [planned("A", 1), planned("A", 2)],
        params=ScriptedParams(max_holding_days=5),
    )
    assert len(result.trades) == 1
    assert result.skipped["already_holding"] == 1


def test_position_size_is_capped_at_max_weight() -> None:
    # 평가액 1,000만 × 10% = 100만 / 시가 100 → 10,000주
    t = run({"A": flat_bars(DAYS)}, [planned("A", 2)]).trades[0]
    assert t.qty == 10_000


def test_net_return_with_costs_hand_calculated() -> None:
    costs = CostConfig(
        buy_fee_rate=Decimal("0.00015"),
        sell_fee_rate=Decimal("0.00015"),
        sell_tax_rate=Decimal("0.002"),
        slippage_bps=Decimal(10),
    )
    bars = with_day(flat_bars(DAYS, price=10_000), 4, bar(DAYS[4], 10_000, 11_000, 10_000, 10_500))
    t = run({"A": bars}, [planned("A", 2)], costs=costs).trades[0]
    # 진입 10,000 → 목표 11,000 에 익절. 수량 = floor(1,000,000 / 10,011.5015) = 99
    assert (t.exit_reason, t.qty) == ("TAKE_PROFIT", 99)
    assert t.gross_return == Decimal("0.1")
    # 비용 후 수익률은 수량과 무관: 10,989×(1-0.00215) / (10,010×1.00015) - 1
    expected = (
        Decimal(10_989) * (1 - Decimal("0.00215")) / (Decimal(10_010) * Decimal("1.00015")) - 1
    )
    assert t.net_return == pytest.approx(expected)
    assert round(t.net_return, 6) == Decimal("0.095278")


def test_equity_curve_marks_to_market_at_close() -> None:
    bars = with_day(flat_bars(DAYS), 3, bar(DAYS[3], 100, 104, 100, 103))
    result = run({"A": bars}, [planned("A", 2)])
    # DAYS[3] 종가 103: 현금 900만 + 10,000주 × 103 = 1,003만
    assert dict(result.equity_curve)[DAYS[3]] == Decimal(10_030_000)
