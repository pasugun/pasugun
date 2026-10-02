"""비용 계산. 기대값은 손계산."""

from decimal import Decimal

from engine.backtest import costs
from engine.backtest.config import CostConfig

COSTS = CostConfig(
    buy_fee_rate=Decimal("0.00015"),
    sell_fee_rate=Decimal("0.00015"),
    sell_tax_rate=Decimal("0.002"),
    slippage_bps=Decimal(10),
)


def test_buy() -> None:
    # 10,000원 × (1 + 0.001) = 10,010 / 10주 = 100,100 / 수수료 100,100 × 0.00015 = 15.015
    b = costs.buy(10, Decimal(10_000), COSTS)
    assert (b.fill_price, b.amount, b.fee, b.total) == (
        Decimal("10010"),
        Decimal("100100"),
        Decimal("15.015"),
        Decimal("100115.015"),
    )


def test_sell() -> None:
    # 11,000 × (1 - 0.001) = 10,989 / 10주 = 109,890
    # 수수료 109,890 × 0.00015 = 16.4835, 세금 109,890 × 0.002 = 219.78
    # 순입금 = 109,890 - 16.4835 - 219.78 = 109,653.7365
    s = costs.sell(10, Decimal(11_000), COSTS)
    assert (s.fill_price, s.amount, s.fee, s.tax, s.net) == (
        Decimal("10989"),
        Decimal("109890"),
        Decimal("16.4835"),
        Decimal("219.78"),
        Decimal("109653.7365"),
    )


def test_round_trip_net_return() -> None:
    # 109,653.7365 / 100,115.015 - 1 = 0.0952776…  (비용 전 +10% → 비용 후 약 +9.53%)
    b = costs.buy(10, Decimal(10_000), COSTS)
    s = costs.sell(10, Decimal(11_000), COSTS)
    net = s.net / b.total - 1
    assert round(net, 6) == Decimal("0.095278")


def test_max_qty_includes_slippage_and_fee() -> None:
    # 1주 비용 = 10,010 × 1.00015 = 10,011.5015 → 1,000,000 / 10,011.5015 = 99.88… → 99주
    assert costs.max_qty(Decimal(1_000_000), Decimal(10_000), COSTS) == 99
    assert costs.max_qty(Decimal(10_000), Decimal(10_000), COSTS) == 0
    assert costs.max_qty(Decimal(1_000_000), Decimal(10_000), CostConfig.zero()) == 100


def test_etf_has_no_sell_tax() -> None:
    assert COSTS.without_tax().sell_tax_rate == 0
    assert costs.sell(1, Decimal(100), COSTS.without_tax()).tax == 0
