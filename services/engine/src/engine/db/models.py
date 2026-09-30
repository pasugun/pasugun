"""DB 스키마. 가격은 Numeric(Decimal), 원 단위 금액은 BigInteger, 시각은 timestamptz."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 제약조건 이름을 고정해 Alembic 마이그레이션이 결정적으로 생성되게 한다.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

PRICE = Numeric(20, 4)
PCT = Numeric(12, 6)  # 수익률(%) 등 비율

SIGNAL_STATUSES = ("OPEN", "TAKE_PROFIT", "STOP_LOSS", "EXPIRED", "CANCELLED")
SIGNAL_SOURCES = ("live", "backtest", "paper")


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


def _now() -> Any:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {dict[str, Any]: JSONB, datetime: DateTime(timezone=True)}


# --- 종목·시세 ---------------------------------------------------------------


class Stock(Base):
    __tablename__ = "stocks"

    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    market: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    is_common_share: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    list_date: Mapped[date | None] = mapped_column(Date)
    delist_date: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = _now()


class CandleDaily(Base):
    """일봉. 상장폐지 종목도 담을 수 있게 stocks FK는 두지 않는다(3단계 pykrx 보충)."""

    __tablename__ = "candles_daily"

    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[Decimal] = mapped_column(PRICE)
    high: Mapped[Decimal] = mapped_column(PRICE)
    low: Mapped[Decimal] = mapped_column(PRICE)
    close: Mapped[Decimal] = mapped_column(PRICE)
    volume: Mapped[int] = mapped_column(BigInteger)
    adjusted: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(Text, server_default=text("'toss'"))
    updated_at: Mapped[datetime] = _now()


class InvestorTradingDaily(Base):
    """투자자별 일별 순매수(주식 수)."""

    __tablename__ = "investor_trading_daily"

    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    foreigner_net: Mapped[int] = mapped_column(BigInteger)
    institution_net: Mapped[int] = mapped_column(BigInteger)
    individual_net: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = _now()


# --- 로봇 ---------------------------------------------------------------------


class Robot(Base):
    __tablename__ = "robots"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    style: Mapped[str] = mapped_column(Text)
    holding_period: Mapped[str] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    enabled_globally: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    __table_args__ = (
        CheckConstraint(_in("risk_level", ("low", "medium", "high")), name="risk_level"),
    )


class RobotSetting(Base):
    """사용자가 켠 로봇. 활성 개수 최대 3개는 서비스 계층에서 강제한다(5단계)."""

    __tablename__ = "robot_settings"

    robot_id: Mapped[str] = mapped_column(ForeignKey("robots.id"), primary_key=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    activated_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = _now()


class Watchlist(Base):
    __tablename__ = "watchlists"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    robot_id: Mapped[str] = mapped_column(ForeignKey("robots.id"), primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    score: Mapped[Decimal | None] = mapped_column(PCT)  # 200개 초과 시 우선순위 판단용
    reason: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))


# --- 신호 ---------------------------------------------------------------------


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    robot_id: Mapped[str] = mapped_column(ForeignKey("robots.id"))
    symbol: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'OPEN'"))
    source: Mapped[str] = mapped_column(Text)
    entry_price: Mapped[Decimal] = mapped_column(PRICE)
    target_price: Mapped[Decimal | None] = mapped_column(PRICE)
    stop_price: Mapped[Decimal | None] = mapped_column(PRICE)
    expires_at: Mapped[datetime | None]
    opened_at: Mapped[datetime]
    closed_at: Mapped[datetime | None]
    close_price: Mapped[Decimal | None] = mapped_column(PRICE)
    return_pct: Mapped[Decimal | None] = mapped_column(PCT)
    reason: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    explanation: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(_in("status", SIGNAL_STATUSES), name="status"),
        CheckConstraint(_in("source", SIGNAL_SOURCES), name="source"),
        # 같은 로봇·같은 종목·같은 출처에는 OPEN 신호가 하나만 존재한다.
        Index(
            "uq_signals_one_open_per_robot_symbol",
            "robot_id",
            "symbol",
            "source",
            unique=True,
            postgresql_where=text("status = 'OPEN'"),
        ),
        Index("ix_signals_status_opened_at", "status", "opened_at"),
    )


class SignalEvent(Base):
    __tablename__ = "signal_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    signal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(Text)
    price: Mapped[Decimal | None] = mapped_column(PRICE)
    occurred_at: Mapped[datetime]
    payload: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))


class AlertSent(Base):
    """중복 전송 방지용 기록. dedupe_key 예: '{signal_id}:{event_type}'."""

    __tablename__ = "alerts_sent"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dedupe_key: Mapped[str] = mapped_column(Text, unique=True)
    channel: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = _now()
    payload: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))


class PaperPosition(Base):
    __tablename__ = "paper_positions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    signal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("signals.id"), unique=True)
    qty: Mapped[int] = mapped_column(BigInteger)
    entry_price: Mapped[Decimal] = mapped_column(PRICE)
    exit_price: Mapped[Decimal | None] = mapped_column(PRICE)
    pnl: Mapped[Decimal | None] = mapped_column(PRICE)
    opened_at: Mapped[datetime] = _now()
    closed_at: Mapped[datetime | None]


# --- 백테스트 -----------------------------------------------------------------


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    robot_id: Mapped[str] = mapped_column(ForeignKey("robots.id"))
    params: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    split_date: Mapped[date | None] = mapped_column(Date)  # 학습/검증 구간 경계
    created_at: Mapped[datetime] = _now()
    metrics: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))


class BacktestTrade(Base):
    __tablename__ = "backtest_trades"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(Text)
    entry_date: Mapped[date] = mapped_column(Date)
    entry_price: Mapped[Decimal] = mapped_column(PRICE)
    exit_date: Mapped[date | None] = mapped_column(Date)
    exit_price: Mapped[Decimal | None] = mapped_column(PRICE)
    return_pct: Mapped[Decimal | None] = mapped_column(PCT)
    exit_reason: Mapped[str | None] = mapped_column(Text)
    segment: Mapped[str | None] = mapped_column(Text)  # 'train' | 'test'


# --- 인증 토큰 ----------------------------------------------------------------


class ApiToken(Base):
    """외부 API 토큰. 모든 프로세스가 공유한다. 값은 절대 로그에 남기지 않는다."""

    __tablename__ = "api_tokens"

    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    access_token: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime]
    updated_at: Mapped[datetime] = _now()

    def __repr__(self) -> str:
        return (
            f"ApiToken(provider={self.provider!r}, access_token='***', "
            f"expires_at={self.expires_at!r})"
        )


# --- 2단계(내 계좌 연동)용 ----------------------------------------------------


class AssetSnapshot(Base):
    __tablename__ = "asset_snapshots"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    market_value: Mapped[int] = mapped_column(BigInteger)  # 원
    cash: Mapped[int] = mapped_column(BigInteger)  # 원
    total: Mapped[int] = mapped_column(BigInteger)  # 원
    created_at: Mapped[datetime] = _now()


class CashFlow(Base):
    __tablename__ = "cash_flows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[int] = mapped_column(BigInteger)  # 원, 입금 +, 출금 -
    memo: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class HoldingAlertState(Base):
    __tablename__ = "holding_alert_state"

    symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    avg_price: Mapped[Decimal] = mapped_column(PRICE)
    last_band: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = _now()
