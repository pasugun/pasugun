"""백테스트 CLI.

    uv run backtest run surge_entry --from 2023-01-01 --to 2025-12-31 --split 2025-01-01
    uv run backtest run oversold_rebound --from 2023-01-01 --to 2025-12-31 \
        --param max_holding_days=10

결과는 backtest_runs / backtest_trades 에 저장된다(--no-save 로 끄기).
파라미터는 로봇 기본값을 쓴다. --param 으로 바꾼 값은 결과와 함께 저장된다.
"""

import argparse
import asyncio
import json
import logging
import sys
import unicodedata
from datetime import date, timedelta
from decimal import Decimal

from engine.backtest.benchmark import MissingBenchmarkData
from engine.backtest.config import BacktestConfig
from engine.backtest.runner import BacktestReport, SignalPass, run_segments, scan_series
from engine.backtest.store import backtest_symbols, iter_series, load_series, save_report
from engine.config import get_settings
from engine.db.session import make_async_engine, make_async_sessionmaker
from engine.progress import ProgressPrinter
from engine.robots.registry import STRATEGIES, get_strategy

# 지표 워밍업용으로 시작일보다 앞서 읽는 기간(달력 일수). 60일 최고가 + 여유.
WARMUP_DAYS = 150


def _pct(value: Decimal | str | None) -> str:
    return "-" if value is None else f"{Decimal(value) * 100:,.2f}%"


def _num(value: Decimal | str | int | None, digits: int = 1) -> str:
    return "-" if value is None else f"{Decimal(value):,.{digits}f}"


def _width(text: str) -> int:
    """터미널 표시 폭(한글 등 전각 문자는 2칸)."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * (width - _width(text))


def format_report(report: BacktestReport) -> str:
    segs = report.segments
    names = {"train": "학습", "test": "검증", "full": "전체"}
    header = ["지표", *(f"{names[s.segment.name]} {s.segment.start}~{s.segment.end}" for s in segs)]
    rows: list[list[str]] = [
        ["총수익률 (비용 후)", *(_pct(s.net.total_return) for s in segs)],
        ["총수익률 (비용 전)", *(_pct(s.gross.total_return) for s in segs)],
        ["CAGR (비용 후)", *(_pct(s.net.cagr) for s in segs)],
        ["MDD (비용 후)", *(_pct(s.net.mdd) for s in segs)],
        ["승률", *(_pct(s.net.win_rate) for s in segs)],
        ["평균 수익 (이긴 거래)", *(_pct(s.net.avg_win) for s in segs)],
        ["평균 손실 (진 거래)", *(_pct(s.net.avg_loss) for s in segs)],
        ["거래 횟수", *(str(s.net.trade_count) for s in segs)],
        ["평균 보유일", *(_num(s.net.avg_holding_days) for s in segs)],
        ["KODEX 200 보유 (비용 후)", *(_pct(s.benchmark_net.total_return) for s in segs)],
        ["KODEX 200 MDD", *(_pct(s.benchmark_net.mdd) for s in segs)],
        ["초과수익 (비용 후)", *(_pct(s.excess_net) for s in segs)],
        ["초과수익 (비용 전)", *(_pct(s.excess_gross) for s in segs)],
        ["관문", *("통과" if s.gate.passed else "미통과" for s in segs)],
    ]
    widths = [max(_width(r[i]) for r in [header, *rows]) for i in range(len(header))]
    lines = [" | ".join(_pad(c, w) for c, w in zip(header, widths, strict=True))]
    lines.append("-+-".join("-" * w for w in widths))
    lines += [" | ".join(_pad(c, w) for c, w in zip(r, widths, strict=True)) for r in rows]

    gate = report.gate_segment
    failed = [k for k, ok in gate.gate.checks.items() if not ok]
    lines.append("")
    lines.append(
        f"관문 판정({names[gate.segment.name]} 구간): "
        + ("통과" if gate.gate.passed else f"미통과 — 실패 항목: {', '.join(failed)}")
    )
    skipped = {k: v for s in segs for k, v in s.sim.skipped.items()}
    if skipped:
        lines.append(f"진입 건너뜀(전체 구간 기준 아님, 구간별 합): {skipped}")
    return "\n".join(lines)


def parse_params(pairs: list[str]) -> dict[str, str]:
    out = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"--param 은 key=value 형식입니다: {pair}")
        out[key] = value
    return out


async def cmd_run(args: argparse.Namespace) -> int:
    settings = get_settings()
    config = BacktestConfig.from_settings(settings)
    strategy = get_strategy(args.robot, parse_params(args.param))
    load_from = args.from_ - timedelta(days=WARMUP_DAYS)

    engine = make_async_engine()
    sessions = make_async_sessionmaker(engine)
    try:
        async with sessions() as session:
            benchmark = await load_series(
                session, config.benchmark_symbol, load_from, args.to, with_flows=False
            )
            market_days = [b.date for b in benchmark.bars]
            if not market_days:
                print(
                    f"벤치마크 {config.benchmark_symbol} 일봉이 없습니다. "
                    "`uv run collect backfill` 을 먼저 실행하세요.",
                    file=sys.stderr,
                )
                return 1
            symbols = await backtest_symbols(session)
            print(
                f"{strategy.name}({strategy.id}) 신호 생성: {len(symbols)}종목 "
                f"{args.from_}~{args.to}",
                file=sys.stderr,
            )
            progress = ProgressPrinter("signals", unit="신호")
            signal_pass = SignalPass()
            i = 0
            async for series in iter_series(session, symbols, load_from, args.to):
                entries = scan_series(
                    strategy,
                    series,
                    start=args.from_,
                    end=args.to,
                    market_days=market_days,
                    config=config,
                )
                signal_pass.add(series, entries)
                i += 1
                progress(i, len(symbols), series.symbol, len(entries))
            try:
                report = run_segments(
                    strategy,
                    signal_pass,
                    benchmark,
                    market_days,
                    start=args.from_,
                    end=args.to,
                    split=args.split,
                    config=config,
                )
            except MissingBenchmarkData as exc:
                print(str(exc), file=sys.stderr)
                return 1
            print(format_report(report))
            if args.json:
                print(json.dumps(report.metrics_json(), ensure_ascii=False, indent=2))
            if not args.no_save:
                run_id = await save_report(session, report)
                print(f"\n저장됨: backtest_runs.id = {run_id}")
    finally:
        await engine.dispose()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="backtest", description="로봇 백테스트")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="백테스트 실행")
    run.add_argument("robot", choices=sorted(STRATEGIES))
    run.add_argument("--from", dest="from_", type=date.fromisoformat, required=True)
    run.add_argument("--to", type=date.fromisoformat, required=True)
    run.add_argument(
        "--split",
        type=date.fromisoformat,
        default=None,
        help="이 날부터 검증 구간. 없으면 전체 구간만",
    )
    run.add_argument("--param", action="append", default=[], help="로봇 파라미터 key=value")
    run.add_argument("--json", action="store_true", help="지표 JSON 도 출력")
    run.add_argument("--no-save", action="store_true")
    return parser


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    args = build_parser().parse_args()
    sys.exit(asyncio.run(cmd_run(args)))


if __name__ == "__main__":
    main()
