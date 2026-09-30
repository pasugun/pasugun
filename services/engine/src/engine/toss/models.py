"""토스 API 응답 모델. 필드 이름은 docs/toss/openapi.json 그대로(camelCase) 쓴다.

- decimal 문자열("72000")은 Decimal 로 파싱한다.
- enum 값은 스펙에 새 값이 추가돼도 깨지지 않도록 str 로 받는다(스펙: unknown 값을 허용할 것).
- 스펙에 없는 필드가 와도 무시한다.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class TossModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


# --- Auth ---------------------------------------------------------------------


class OAuth2TokenResponse(TossModel):
    access_token: str
    token_type: str
    expires_in: int

    def __repr__(self) -> str:
        return f"OAuth2TokenResponse(access_token='***', expires_in={self.expires_in})"

    __str__ = __repr__


# --- Account ------------------------------------------------------------------


class Account(TossModel):
    accountNo: str
    accountSeq: int
    accountType: str  # BROKERAGE | OVERSEAS_DERIVATIVES | PENSION_SAVINGS | RESHORING_INVESTMENT


# --- Stock Info ---------------------------------------------------------------


class StockListItem(TossModel):
    """GET /api/v1/stocks/all 항목."""

    symbol: str
    name: str
    securityType: str
    isCommonShare: bool
    isinCode: str


class KrMarketDetail(TossModel):
    liquidationTrading: bool
    nxtSupported: bool
    krxTradingSuspended: bool
    nxtTradingSuspended: bool | None = None


class Stock(TossModel):
    """GET /api/v1/stocks 항목."""

    symbol: str
    name: str
    englishName: str
    isinCode: str
    market: str  # KOSPI | KOSDAQ | NYSE | NASDAQ | AMEX | KR_ETC | US_ETC
    securityType: str
    isCommonShare: bool
    status: str  # SCHEDULED | ACTIVE | DELISTED
    currency: str
    listDate: date | None = None
    delistDate: date | None = None
    sharesOutstanding: Decimal
    leverageFactor: Decimal | None = None
    koreanMarketDetail: KrMarketDetail | None = None


class StockWarning(TossModel):
    warningType: str
    exchange: str | None = None
    startDate: date | None = None
    endDate: date | None = None


class InvestorTradingVolume(TossModel):
    buyVolume: Decimal
    sellVolume: Decimal
    netBuyVolume: Decimal


class StockInstitutionTradingVolume(InvestorTradingVolume):
    breakdown: dict[str, InvestorTradingVolume] | None = None


class StockInvestorTradingRecord(TossModel):
    date: date
    updatedAt: datetime
    individual: InvestorTradingVolume | None = None  # 당일 잠정치에는 null
    foreigner: InvestorTradingVolume
    institution: StockInstitutionTradingVolume
    otherCorporation: InvestorTradingVolume | None = None
    foreignerHolding: dict[str, Any] | None = None
    cfd: dict[str, Any] | None = None


class StockInvestorTradingPage(TossModel):
    nextUntil: date | None = None
    records: list[StockInvestorTradingRecord]


# --- Market Data --------------------------------------------------------------


class Price(TossModel):
    symbol: str
    timestamp: datetime | None = None
    lastPrice: Decimal
    currency: str


class Candle(TossModel):
    timestamp: datetime
    openPrice: Decimal
    highPrice: Decimal
    lowPrice: Decimal
    closePrice: Decimal
    volume: Decimal
    currency: str


class CandlePage(TossModel):
    candles: list[Candle]  # 최신순
    nextBefore: datetime | None = None


# --- Market Info --------------------------------------------------------------


class PreMarketSession(TossModel):
    startTime: datetime
    singlePriceAuctionStartTime: datetime | None = None
    endTime: datetime


class RegularMarketSession(TossModel):
    startTime: datetime
    singlePriceAuctionStartTime: datetime | None = None
    endTime: datetime


class AfterMarketSession(TossModel):
    startTime: datetime
    singlePriceAuctionEndTime: datetime | None = None
    endTime: datetime


class IntegratedHour(TossModel):
    preMarket: PreMarketSession | None = None
    regularMarket: RegularMarketSession | None = None
    afterMarket: AfterMarketSession | None = None


class KrMarketDay(TossModel):
    date: date
    integrated: IntegratedHour | None = None  # KRX·NXT 모두 휴장이면 null

    @property
    def is_open(self) -> bool:
        return self.integrated is not None and self.integrated.regularMarket is not None


class KrMarketCalendar(TossModel):
    today: KrMarketDay
    previousBusinessDay: KrMarketDay
    nextBusinessDay: KrMarketDay


# --- Ranking ------------------------------------------------------------------


class RankingPrice(TossModel):
    lastPrice: Decimal
    basePrice: Decimal
    changeRate: Decimal | None = None  # 소수비율 (0.0125 = 1.25%)


class RankingItem(TossModel):
    rank: int
    symbol: str
    currency: str
    price: RankingPrice
    tradingVolume: Decimal
    tradingAmount: Decimal


class RankingPage(TossModel):
    rankedAt: datetime | None = None
    rankings: list[RankingItem]


# --- Asset --------------------------------------------------------------------


class CurrencyAmount(TossModel):
    krw: Decimal
    usd: Decimal | None = None


class HoldingsMarketValue(TossModel):
    amount: CurrencyAmount
    amountAfterCost: CurrencyAmount


class HoldingsProfitLoss(TossModel):
    amount: CurrencyAmount
    amountAfterCost: CurrencyAmount
    rate: Decimal
    rateAfterCost: Decimal


class HoldingsDailyProfitLoss(TossModel):
    amount: CurrencyAmount
    rate: Decimal


class HoldingItemMarketValue(TossModel):
    purchaseAmount: Decimal
    amount: Decimal
    amountAfterCost: Decimal


class HoldingItemProfitLoss(TossModel):
    amount: Decimal
    amountAfterCost: Decimal
    rate: Decimal
    rateAfterCost: Decimal


class HoldingItemDailyProfitLoss(TossModel):
    amount: Decimal
    rate: Decimal


class HoldingItemCost(TossModel):
    commission: Decimal
    tax: Decimal | None = None


class HoldingItem(TossModel):
    symbol: str
    name: str
    marketCountry: str
    currency: str
    quantity: Decimal
    lastPrice: Decimal
    averagePurchasePrice: Decimal
    marketValue: HoldingItemMarketValue
    profitLoss: HoldingItemProfitLoss
    dailyProfitLoss: HoldingItemDailyProfitLoss
    cost: HoldingItemCost


class Holdings(TossModel):
    totalPurchaseAmount: CurrencyAmount
    marketValue: HoldingsMarketValue
    profitLoss: HoldingsProfitLoss
    dailyProfitLoss: HoldingsDailyProfitLoss
    items: list[HoldingItem]


# --- Order Info / History (조회 전용) -----------------------------------------


class BuyingPower(TossModel):
    currency: str
    cashBuyingPower: Decimal


class OrderExecution(TossModel):
    filledQuantity: Decimal
    averageFilledPrice: Decimal | None = None
    filledAmount: Decimal | None = None
    commission: Decimal | None = None
    tax: Decimal | None = None
    filledAt: datetime | None = None
    settlementDate: date | None = None


class Order(TossModel):
    orderId: str
    symbol: str
    side: str  # BUY | SELL
    orderType: str
    timeInForce: str
    status: str
    price: Decimal | None = None
    quantity: Decimal
    orderAmount: Decimal | None = None
    currency: str
    orderedAt: datetime
    canceledAt: datetime | None = None
    execution: OrderExecution


class OrderPage(TossModel):
    orders: list[Order]
    nextCursor: str | None = None
    hasNext: bool
