"""로봇(Strategy) 인터페이스.

시점 규칙(미래 데이터 참조 금지):
- check_entry_daily 는 D 일 장 마감까지의 데이터로 판단한다. 체결은 백테스터가 D+1 시가로 한다.
- check_exit_daily 도 D 장 마감 기준이며, 청산은 D+1 시가로 한다.
- 목표가·손절가·보유기간 청산은 ExitRule 로 미리 정해 두고 백테스터가 장중 고가·저가로 판정한다.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel, ConfigDict

from engine.robots.data import SymbolView


class StrategyParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def jsonable(value: Any) -> Any:
    """reason 저장용. Decimal 은 문자열로."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    if isinstance(value, date):
        return value.isoformat()
    return value


@dataclass(frozen=True)
class Candidate:
    """장 마감 후 고른 '내일 실시간으로 볼' 후보."""

    symbol: str
    score: Decimal
    reason: dict[str, Any]


@dataclass(frozen=True)
class EntrySignal:
    symbol: str
    date: date  # 판단 시점(장 마감)
    reference_price: Decimal  # 판단 시점 가격(일봉이면 종가)
    score: Decimal  # 같은 날 여러 신호일 때 우선순위(클수록 먼저)
    reason: dict[str, Any]  # 충족한 조건과 실제 수치
    levels: dict[str, Decimal] = field(default_factory=dict)  # 청산 규칙에 쓸 기준값


@dataclass(frozen=True)
class ExitRule:
    target_price: Decimal | None  # 이 가격 이상이면 익절
    stop_price: Decimal | None  # 이 가격 이하면 손절
    max_holding_days: int  # 진입일을 1일째로 센 거래일 수. 그날 종가에 청산
    trailing: bool = False  # TODO: 추적 손절은 아직 백테스터가 지원하지 않는다


@dataclass(frozen=True)
class OpenPosition:
    """check_exit_daily 에 넘기는 보유 정보."""

    symbol: str
    entry_date: date
    entry_price: Decimal
    held_days: int
    signal: EntrySignal


class IntradaySnapshot(Protocol):
    """5단계 실시간 워커가 채운다."""

    symbol: str
    last_price: Decimal
    day_volume: int
    day_high: Decimal
    day_low: Decimal


class MarketData(Protocol):
    def view(self, symbol: str, day: date) -> SymbolView | None: ...

    def symbols(self) -> list[str]: ...

    def is_excluded(self, symbol: str, day: date) -> bool:
        """매수 유의·거래정지 등으로 후보에서 뺄 종목인지."""
        ...


class Strategy[P: StrategyParams](ABC):
    id: ClassVar[str]
    name: ClassVar[str]
    params_model: ClassVar[type[StrategyParams]]

    def __init__(self, params: P | None = None) -> None:
        self.params: P = params or self.params_model()  # type: ignore[assignment]

    @property
    def min_bars(self) -> int:
        """판단에 필요한 최소 봉 수(워밍업). 이보다 짧으면 신호를 내지 않는다."""
        return 1

    @classmethod
    def from_overrides(cls, overrides: dict[str, Any] | None) -> "Strategy[Any]":
        return cls(cls.params_model(**(overrides or {})))  # type: ignore[arg-type]

    @abstractmethod
    def check_entry(self, view: SymbolView) -> EntrySignal | None:
        """view 시점(장 마감) 기준 진입 조건 판단. 순수 함수여야 한다."""

    @abstractmethod
    def exit_rule(self, signal: EntrySignal, entry_price: Decimal) -> ExitRule: ...

    @abstractmethod
    def candidate_score(self, view: SymbolView) -> Candidate | None:
        """내일 감시할 만한 '준비 단계' 종목이면 Candidate."""

    def check_exit_daily(self, view: SymbolView, position: OpenPosition) -> str | None:
        """지표 기반 청산(장 마감 판단 → 다음 날 시가 청산). 기본은 없음."""
        return None

    # --- 기획서 인터페이스 ---------------------------------------------------

    def check_entry_daily(self, symbol: str, day: date, data: MarketData) -> EntrySignal | None:
        if data.is_excluded(symbol, day):
            return None
        view = data.view(symbol, day)
        if view is None or view.available < self.min_bars:
            return None
        return self.check_entry(view)

    def select_candidates(self, day: date, data: MarketData) -> list[Candidate]:
        out = []
        for symbol in data.symbols():
            if data.is_excluded(symbol, day):
                continue
            view = data.view(symbol, day)
            if view is None or view.available < self.min_bars:
                continue
            candidate = self.candidate_score(view)
            if candidate is not None:
                out.append(candidate)
        return sorted(out, key=lambda c: (-c.score, c.symbol))

    def check_entry_intraday(self, symbol: str, snapshot: IntradaySnapshot) -> EntrySignal | None:
        # TODO(5단계): 실시간 스냅샷 기준 진입 판단
        raise NotImplementedError
