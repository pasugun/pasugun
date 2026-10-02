from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from engine.config import Settings

BPS = Decimal(10_000)


class CostConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    buy_fee_rate: Decimal
    sell_fee_rate: Decimal
    sell_tax_rate: Decimal
    slippage_bps: Decimal

    @property
    def slippage(self) -> Decimal:
        return self.slippage_bps / BPS

    @classmethod
    def zero(cls) -> "CostConfig":
        return cls(buy_fee_rate=0, sell_fee_rate=0, sell_tax_rate=0, slippage_bps=0)

    def without_tax(self) -> "CostConfig":
        """ETF 는 매도 거래세가 없다(벤치마크용)."""
        return self.model_copy(update={"sell_tax_rate": Decimal(0)})


class PortfolioConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    initial_capital: Decimal
    max_positions: int
    max_weight: Decimal


class UniverseConfig(BaseModel):
    """그날까지의 데이터로 계산한 대상 종목 조건(시점별)."""

    model_config = ConfigDict(frozen=True)

    min_avg_trading_value: Decimal
    avg_days: int


class GateConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_excess_return: Decimal
    mdd_floor: Decimal
    min_trades: int


class BacktestConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    costs: CostConfig
    portfolio: PortfolioConfig
    universe: UniverseConfig
    gate: GateConfig
    benchmark_symbol: str

    @classmethod
    def from_settings(cls, s: Settings) -> "BacktestConfig":
        return cls(
            costs=CostConfig(
                buy_fee_rate=s.backtest_buy_fee_rate,
                sell_fee_rate=s.backtest_sell_fee_rate,
                sell_tax_rate=s.backtest_sell_tax_rate,
                slippage_bps=s.backtest_slippage_bps,
            ),
            portfolio=PortfolioConfig(
                initial_capital=s.backtest_initial_capital,
                max_positions=s.backtest_max_positions,
                max_weight=s.backtest_max_weight,
            ),
            universe=UniverseConfig(
                min_avg_trading_value=Decimal(s.collect_min_avg_trading_value),
                avg_days=s.collect_avg_trading_value_days,
            ),
            gate=GateConfig(
                min_excess_return=s.gate_min_excess_return,
                mdd_floor=s.gate_mdd_floor,
                min_trades=s.gate_min_trades,
            ),
            benchmark_symbol=s.backtest_benchmark_symbol,
        )
