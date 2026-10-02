"""실행·저장 통합 테스트: 구간 분리, 비용 전후, 벤치마크, DB 저장."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from engine.backtest.config import BacktestConfig
from engine.backtest.runner import build_segments, run_backtest
from engine.backtest.store import load_series, save_report
from engine.db.models import BacktestRun, BacktestTrade, CandleDaily, InvestorTradingDaily
from engine.robots.data import Bar
from tests.backtest.scripted import Scripted, ScriptedParams
from tests.collect.fakes import make_settings
from tests.series_helpers import bar, flat_bars, series, trading_days

DAYS = trading_days(40)
SPLIT = DAYS[20]
CONFIG = BacktestConfig.from_settings(make_settings()).model_copy(
    update={
        "universe": BacktestConfig.from_settings(make_settings()).universe.model_copy(
            update={"min_avg_trading_value": Decimal(0), "avg_days": 1}
        )
    }
)


def rising(days, start=100, step=1) -> list[Bar]:
    return [
        bar(d, start + i * step, start + i * step + 1, start + i * step - 1, start + i * step)
        for i, d in enumerate(days)
    ]


def test_build_segments() -> None:
    train, test, full = build_segments(DAYS[0], DAYS[-1], SPLIT)
    assert (train.end, test.start) == (SPLIT - timedelta(days=1), SPLIT)
    assert (full.start, full.end) == (DAYS[0], DAYS[-1])
    with pytest.raises(ValueError):
        build_segments(DAYS[0], DAYS[-1], DAYS[0])


def test_run_backtest_segments_costs_and_benchmark() -> None:
    strategy = Scripted({"AAA": {DAYS[3], DAYS[25]}}, ScriptedParams(max_holding_days=3))
    benchmark = series("069500", flat_bars(DAYS, price=10_000))
    report = run_backtest(
        strategy,
        [series("AAA", rising(DAYS))],
        benchmark,
        start=DAYS[0],
        end=DAYS[-1],
        split=SPLIT,
        config=CONFIG,
    )

    train, test, full = (report.segment(n) for n in ("train", "test", "full"))
    assert [t.entry_date for t in train.sim.trades] == [DAYS[4]]
    assert [t.entry_date for t in test.sim.trades] == [DAYS[26]]
    assert len(full.sim.trades) == 2
    # 오르는 종목을 3일 보유: 비용 전 수익 > 비용 후 수익
    assert full.gross.total_return > full.net.total_return > 0
    # 가격이 그대로인 벤치마크: 비용 전 0%, 비용 후 음수
    assert full.benchmark_gross.total_return == 0
    assert full.benchmark_net.total_return < 0
    assert full.excess_net == full.net.total_return - full.benchmark_net.total_return
    assert report.gate_segment is test
    assert test.gate.checks["trade_count"] is False  # 거래 1회 < 100


async def test_load_series_and_save_report(session) -> None:
    for b in rising(DAYS):
        session.add(
            CandleDaily(
                symbol="AAA",
                date=b.date,
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
                adjusted=True,
                source="toss",
            )
        )
    session.add(
        InvestorTradingDaily(
            symbol="AAA", date=DAYS[3], foreigner_net=5, institution_net=-2, individual_net=None
        )
    )
    await session.commit()

    loaded = await load_series(session, "AAA", DAYS[0], DAYS[-1])
    assert [b.close for b in loaded.bars] == [b.close for b in rising(DAYS)]
    assert loaded.flows[DAYS[3]].foreigner_net == 5

    strategy = Scripted({"AAA": {DAYS[3]}}, ScriptedParams(max_holding_days=3))
    report = run_backtest(
        strategy,
        [loaded],
        series("069500", flat_bars(DAYS)),
        start=DAYS[0],
        end=DAYS[-1],
        split=SPLIT,
        config=CONFIG,
    )
    run_id = await save_report(session, report)

    run = await session.get(BacktestRun, run_id)
    assert (run.robot_id, run.period_start, run.split_date) == ("surge_entry", DAYS[0], SPLIT)
    assert set(run.metrics["segments"]) == {"train", "test", "full"}
    assert run.params["strategy"]["max_holding_days"] == 3
    trades = (
        await session.scalars(select(BacktestTrade).where(BacktestTrade.run_id == run_id))
    ).all()
    assert sorted(t.segment for t in trades) == ["full", "train"]
