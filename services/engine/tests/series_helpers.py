from datetime import date, timedelta
from decimal import Decimal

from engine.robots.data import Bar, InvestorFlow, SymbolSeries

START = date(2026, 1, 5)  # 월요일


def trading_days(n: int, start: date = START) -> list[date]:
    """start 부터 평일 n 개(오름차순)."""
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def bar(day: date, o, h, low, c, v: int = 1000) -> Bar:
    return Bar(day, Decimal(str(o)), Decimal(str(h)), Decimal(str(low)), Decimal(str(c)), v)


def flat_bars(days: list[date], price=100, volume: int = 1000) -> list[Bar]:
    return [bar(d, price, price, price, price, volume) for d in days]


def series(symbol: str, bars: list[Bar], flows: dict[date, tuple[int, int]] | None = None):
    return SymbolSeries(
        symbol,
        bars,
        {d: InvestorFlow(d, f, i) for d, (f, i) in (flows or {}).items()},
    )


def D(x) -> Decimal:
    return Decimal(str(x))
