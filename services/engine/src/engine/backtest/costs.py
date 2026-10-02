"""체결가·비용 계산. 원 단위 반올림은 하지 않는다(Decimal 그대로, 결정적)."""

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from engine.backtest.config import CostConfig


@dataclass(frozen=True)
class Buy:
    qty: int
    fill_price: Decimal  # 시가 × (1 + 슬리피지)
    amount: Decimal  # qty × fill_price
    fee: Decimal

    @property
    def total(self) -> Decimal:
        """현금에서 빠지는 금액."""
        return self.amount + self.fee


@dataclass(frozen=True)
class Sell:
    qty: int
    fill_price: Decimal  # 청산 기준가 × (1 - 슬리피지)
    amount: Decimal
    fee: Decimal
    tax: Decimal

    @property
    def net(self) -> Decimal:
        """현금으로 들어오는 금액."""
        return self.amount - self.fee - self.tax


def buy_fill(raw_price: Decimal, costs: CostConfig) -> Decimal:
    return raw_price * (1 + costs.slippage)


def max_qty(budget: Decimal, raw_price: Decimal, costs: CostConfig) -> int:
    """budget 안에서 (수수료 포함) 살 수 있는 최대 정수 수량."""
    unit = buy_fill(raw_price, costs) * (1 + costs.buy_fee_rate)
    if budget <= 0 or unit <= 0:
        return 0
    return int((budget / unit).to_integral_value(rounding=ROUND_FLOOR))


def buy(qty: int, raw_price: Decimal, costs: CostConfig) -> Buy:
    fill = buy_fill(raw_price, costs)
    amount = fill * qty
    return Buy(qty, fill, amount, amount * costs.buy_fee_rate)


def sell(qty: int, raw_price: Decimal, costs: CostConfig) -> Sell:
    fill = raw_price * (1 - costs.slippage)
    amount = fill * qty
    return Sell(qty, fill, amount, amount * costs.sell_fee_rate, amount * costs.sell_tax_rate)
