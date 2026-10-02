"""과대낙폭 반등 (스윙, 1~4주).

진입 (D 장 마감 기준, 모두 충족):
- 낙폭: 당일 종가 ≤ 최근 60거래일(당일 포함) 최고 고가 × (1 - 25%)
- RSI(14): 전일 RSI ≤ 30 이고 당일 RSI > 전일 RSI
- 볼린저 하단(20, 2σ) 복귀: 전일 종가 < 전일 하단 이고 당일 종가 ≥ 당일 하단
청산:
- 익절: 장 마감 RSI ≥ 60 이면 다음 날 시가에 청산
  (종가를 보고 그 종가에 파는 건 미래 참조라 다음 날 시가)
- 손절: 진입 판단일까지 최근 20거래일 최저 저가 이하로 내려가면
- 기간: 진입일 포함 20거래일째 종가
"""

from decimal import Decimal

from engine.indicators import bollinger, rolling_max, rolling_min, rsi
from engine.robots.base import (
    Candidate,
    EntrySignal,
    ExitRule,
    OpenPosition,
    Strategy,
    StrategyParams,
    jsonable,
)
from engine.robots.data import SymbolSeries, SymbolView


class OversoldReboundParams(StrategyParams):
    high_lookback_days: int = 60
    drawdown_threshold: Decimal = Decimal("-0.25")
    rsi_period: int = 14
    rsi_oversold: Decimal = Decimal(30)
    bb_period: int = 20
    bb_k: Decimal = Decimal(2)
    exit_rsi: Decimal = Decimal(60)
    stop_lookback_days: int = 20
    max_holding_days: int = 20
    # 후보(내일 감시): 낙폭이 이만큼 이상이고 RSI 가 이 값 이하
    candidate_drawdown: Decimal = Decimal("-0.20")
    candidate_rsi: Decimal = Decimal(35)


class OversoldRebound(Strategy[OversoldReboundParams]):
    id = "oversold_rebound"
    name = "과대낙폭 반등"
    params_model = OversoldReboundParams

    @property
    def min_bars(self) -> int:
        p = self.params
        # 60일 최고가, 전일 볼린저 하단(21봉), 전일 RSI(n+2봉), 20일 최저가
        return max(p.high_lookback_days, p.bb_period + 1, p.rsi_period + 2, p.stop_lookback_days)

    # --- 지표 (전부 인과적) -------------------------------------------------

    def _high_max(self, view: SymbolView) -> Decimal | None:
        n = self.params.high_lookback_days
        return view.value(f"high_max{n}", lambda s: rolling_max(s.field("high"), n))

    def _low_min(self, view: SymbolView) -> Decimal | None:
        n = self.params.stop_lookback_days
        return view.value(f"low_min{n}", lambda s: rolling_min(s.field("low"), n))

    def _rsi(self, view: SymbolView, ago: int = 0) -> Decimal | None:
        n = self.params.rsi_period
        return view.value(f"rsi{n}", lambda s: rsi(s.field("close"), n), ago)

    def _bb_lower(self, view: SymbolView, ago: int = 0) -> Decimal | None:
        n, k = self.params.bb_period, self.params.bb_k

        def compute(s: SymbolSeries):
            return bollinger(s.field("close"), n, k)[2]

        return view.value(f"bb_lower{n}_{k}", compute, ago)

    def _drawdown(self, view: SymbolView) -> Decimal | None:
        today, high = view.bar(), self._high_max(view)
        return today.close / high - 1 if today and high else None

    # --- 진입 ---------------------------------------------------------------

    def check_entry(self, view: SymbolView) -> EntrySignal | None:
        today, prev = view.bar(), view.bar(1)
        drawdown = self._drawdown(view)
        rsi_now, rsi_prev = self._rsi(view), self._rsi(view, 1)
        lower_now, lower_prev = self._bb_lower(view), self._bb_lower(view, 1)
        stop_ref = self._low_min(view)
        values = (today, prev, drawdown, rsi_now, rsi_prev, lower_now, lower_prev, stop_ref)
        if any(v is None for v in values):
            return None
        p = self.params
        conditions = {
            "deep_drawdown": drawdown <= p.drawdown_threshold,
            "rsi_oversold_prev": rsi_prev <= p.rsi_oversold,
            "rsi_rising": rsi_now > rsi_prev,
            "bb_lower_reclaim": prev.close < lower_prev and today.close >= lower_now,
        }
        if not all(conditions.values()):
            return None
        return EntrySignal(
            symbol=view.symbol,
            date=view.date,
            reference_price=today.close,
            score=p.rsi_oversold - rsi_prev,  # 더 깊이 과매도였을수록 먼저
            reason=jsonable(
                {
                    "conditions": conditions,
                    "close": today.close,
                    "high_max": self._high_max(view),
                    "drawdown": drawdown,
                    "rsi_prev": rsi_prev,
                    "rsi": rsi_now,
                    "bb_lower_prev": lower_prev,
                    "bb_lower": lower_now,
                    "prev_close": prev.close,
                    "stop_reference_low": stop_ref,
                }
            ),
            levels={"stop": stop_ref},
        )

    def exit_rule(self, signal: EntrySignal, entry_price: Decimal) -> ExitRule:
        return ExitRule(
            target_price=None,
            stop_price=signal.levels["stop"],
            max_holding_days=self.params.max_holding_days,
        )

    def check_exit_daily(self, view: SymbolView, position: OpenPosition) -> str | None:
        value = self._rsi(view)
        if value is not None and value >= self.params.exit_rsi:
            return "RSI_TAKE_PROFIT"
        return None

    def candidate_score(self, view: SymbolView) -> Candidate | None:
        drawdown, rsi_now = self._drawdown(view), self._rsi(view)
        if drawdown is None or rsi_now is None:
            return None
        p = self.params
        if drawdown > p.candidate_drawdown or rsi_now > p.candidate_rsi:
            return None
        return Candidate(
            view.symbol, p.candidate_rsi - rsi_now, jsonable({"drawdown": drawdown, "rsi": rsi_now})
        )
