"""전략이 보는 시계열 데이터.

SymbolSeries 는 한 종목의 일봉·수급 전체를 갖고, 지표를 전체 구간으로 한 번 계산해 캐시한다.
전략은 SymbolView(시점 t)로만 데이터를 본다. View 는 t 이후 값을 돌려주는 방법이 없고,
지표는 인과적이라(engine.indicators) 전체로 계산한 값의 t 번째 = t 까지로 계산한 값이다.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from engine.indicators import Series, multiply


@dataclass(frozen=True)
class Bar:
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class InvestorFlow:
    date: date
    foreigner_net: int
    institution_net: int
    individual_net: int | None = None


Field = str  # "open" | "high" | "low" | "close" | "volume" | "trading_value"


@dataclass
class SymbolSeries:
    symbol: str
    bars: list[Bar]  # 날짜 오름차순
    flows: dict[date, InvestorFlow] = field(default_factory=dict)
    _index: dict[date, int] = field(init=False, repr=False)
    _fields: dict[Field, list[Decimal]] = field(init=False, repr=False, default_factory=dict)
    _cache: dict[str, Series] = field(init=False, repr=False, default_factory=dict)

    def __post_init__(self) -> None:
        dates = [b.date for b in self.bars]
        if dates != sorted(dates) or len(set(dates)) != len(dates):
            raise ValueError(f"{self.symbol}: 일봉은 날짜 오름차순·중복 없음이어야 합니다")
        self._index = {d: i for i, d in enumerate(dates)}

    def __len__(self) -> int:
        return len(self.bars)

    def index_of(self, day: date) -> int | None:
        return self._index.get(day)

    def field(self, name: Field) -> list[Decimal]:
        if name not in self._fields:
            if name == "trading_value":
                values = multiply(self.field("close"), self.field("volume"))
            else:
                values = [Decimal(getattr(b, name)) for b in self.bars]
            self._fields[name] = values
        return self._fields[name]

    def indicator(self, key: str, compute: Callable[["SymbolSeries"], Series]) -> Series:
        """key 로 캐시. compute 는 반드시 engine.indicators 의 인과적 함수만 써야 한다."""
        if key not in self._cache:
            values = compute(self)
            if len(values) != len(self.bars):
                raise ValueError(f"지표 {key} 길이가 일봉과 다릅니다")
            self._cache[key] = values
        return self._cache[key]

    def view(self, day: date) -> "SymbolView | None":
        i = self.index_of(day)
        return SymbolView(self, i) if i is not None else None


class SymbolView:
    """시점 t(=bars[t].date 장 마감) 기준으로 과거만 보여준다."""

    __slots__ = ("_series", "_t")

    def __init__(self, series: SymbolSeries, t: int) -> None:
        if not 0 <= t < len(series):
            raise IndexError(t)
        self._series = series
        self._t = t

    @property
    def symbol(self) -> str:
        return self._series.symbol

    @property
    def date(self) -> date:
        return self._series.bars[self._t].date

    @property
    def available(self) -> int:
        """t 까지 쌓인 봉 개수."""
        return self._t + 1

    def _past_index(self, ago: int) -> int | None:
        if ago < 0:
            raise ValueError("미래(ago < 0)는 볼 수 없습니다")
        i = self._t - ago
        return i if i >= 0 else None

    def bar(self, ago: int = 0) -> Bar | None:
        """ago=0 오늘, 1 전일 ..."""
        i = self._past_index(ago)
        return self._series.bars[i] if i is not None else None

    def flow(self, ago: int = 0) -> InvestorFlow | None:
        bar = self.bar(ago)
        return self._series.flows.get(bar.date) if bar else None

    def value(
        self, key: str, compute: Callable[[SymbolSeries], Series], ago: int = 0
    ) -> Decimal | None:
        i = self._past_index(ago)
        return self._series.indicator(key, compute)[i] if i is not None else None

    def window(self, name: Field, n: int) -> Sequence[Decimal]:
        """최근 n 개(t 포함). 부족하면 있는 만큼."""
        start = max(0, self._t - n + 1)
        return self._series.field(name)[start : self._t + 1]
