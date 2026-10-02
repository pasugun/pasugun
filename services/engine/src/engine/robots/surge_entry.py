"""수급 초입 (단타, 1~3일).

진입 (D 장 마감 기준, 모두 충족):
- 거래량: 당일 거래량 ≥ 직전 20거래일 평균 거래량 × 3   ※ 평균에 당일은 넣지 않는다
- 돌파: 당일 종가 > 전일 고가
- 수급: 당일 외국인 순매수 > 0 이고 기관 순매수 > 0   ※ 수급 데이터가 없는 날은 신호 없음
청산: 진입가 +5% 익절, -3% 손절, 진입일 포함 3거래일째 종가 청산
"""

from decimal import Decimal

from engine.indicators import shift, sma
from engine.robots.base import Candidate, EntrySignal, ExitRule, Strategy, StrategyParams, jsonable
from engine.robots.data import SymbolSeries, SymbolView


class SurgeEntryParams(StrategyParams):
    volume_avg_days: int = 20
    volume_multiple: Decimal = Decimal(3)
    take_profit_pct: Decimal = Decimal("0.05")
    stop_loss_pct: Decimal = Decimal("0.03")
    max_holding_days: int = 3
    # 후보(내일 감시): 거래량이 평균의 이 배수 이상이고 외국인·기관이 함께 산 종목
    candidate_volume_multiple: Decimal = Decimal("1.5")


class SurgeEntry(Strategy[SurgeEntryParams]):
    id = "surge_entry"
    name = "수급 초입"
    params_model = SurgeEntryParams

    @property
    def min_bars(self) -> int:
        return self.params.volume_avg_days + 1  # 직전 n일 평균 + 당일

    def _avg_prev_volume(self, view: SymbolView) -> Decimal | None:
        n = self.params.volume_avg_days

        def compute(s: SymbolSeries):
            return shift(sma(s.field("volume"), n), 1)

        return view.value(f"volume_sma{n}_prev", compute)

    def check_entry(self, view: SymbolView) -> EntrySignal | None:
        today, prev = view.bar(), view.bar(1)
        avg_volume = self._avg_prev_volume(view)
        flow = view.flow()
        if today is None or prev is None or not avg_volume or flow is None:
            return None

        volume_ratio = Decimal(today.volume) / avg_volume
        conditions = {
            "volume_surge": volume_ratio >= self.params.volume_multiple,
            "breakout": today.close > prev.high,
            "foreigner_buy": flow.foreigner_net > 0,
            "institution_buy": flow.institution_net > 0,
        }
        if not all(conditions.values()):
            return None
        return EntrySignal(
            symbol=view.symbol,
            date=view.date,
            reference_price=today.close,
            score=volume_ratio,
            reason=jsonable(
                {
                    "conditions": conditions,
                    "volume": today.volume,
                    "avg_volume_prev": avg_volume,
                    "volume_ratio": volume_ratio,
                    "close": today.close,
                    "prev_high": prev.high,
                    "foreigner_net": flow.foreigner_net,
                    "institution_net": flow.institution_net,
                }
            ),
        )

    def exit_rule(self, signal: EntrySignal, entry_price: Decimal) -> ExitRule:
        p = self.params
        return ExitRule(
            target_price=entry_price * (1 + p.take_profit_pct),
            stop_price=entry_price * (1 - p.stop_loss_pct),
            max_holding_days=p.max_holding_days,
        )

    def candidate_score(self, view: SymbolView) -> Candidate | None:
        today, avg_volume, flow = view.bar(), self._avg_prev_volume(view), view.flow()
        if today is None or not avg_volume or flow is None:
            return None
        ratio = Decimal(today.volume) / avg_volume
        if ratio < self.params.candidate_volume_multiple:
            return None
        if flow.foreigner_net <= 0 or flow.institution_net <= 0:
            return None
        return Candidate(
            view.symbol,
            ratio,
            jsonable(
                {
                    "volume_ratio": ratio,
                    "high": today.high,
                    "foreigner_net": flow.foreigner_net,
                    "institution_net": flow.institution_net,
                }
            ),
        )
