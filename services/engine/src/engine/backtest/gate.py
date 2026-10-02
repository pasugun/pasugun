"""기획서 관문 판정: 비용 차감 후 벤치마크 대비 초과수익 > 0, MDD > -20%, 거래 ≥ 100 (값은 설정)."""

from dataclasses import dataclass
from decimal import Decimal

from engine.backtest.config import GateConfig


@dataclass(frozen=True)
class GateResult:
    passed: bool
    checks: dict[str, bool]
    excess_return: Decimal
    mdd: Decimal
    trade_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "checks": self.checks,
            "excess_return": str(self.excess_return),
            "mdd": str(self.mdd),
            "trade_count": self.trade_count,
        }


def passes_gate(
    *, net_excess_return: Decimal, mdd: Decimal, trade_count: int, gate: GateConfig
) -> GateResult:
    checks = {
        "excess_return": net_excess_return > gate.min_excess_return,
        "mdd": mdd > gate.mdd_floor,
        "trade_count": trade_count >= gate.min_trades,
    }
    return GateResult(all(checks.values()), checks, net_excess_return, mdd, trade_count)
