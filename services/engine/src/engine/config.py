from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_root_env_file() -> Path | None:
    """모노레포 루트(.env.example 이 있는 곳)의 .env 를 찾는다.

    컨테이너에서는 compose 의 env_file 로 환경변수가 주입되므로 찾지 못해도 된다.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / ".env.example").is_file():
            return parent / ".env"
    return None


ROOT_ENV_FILE = _find_root_env_file()
# services/engine (src/engine/config.py 기준 두 단계 위). 컨테이너에서는 /app.
ENGINE_ROOT = Path(__file__).resolve().parents[2]


class TossMode(StrEnum):
    MOCK = "mock"
    LIVE = "live"


class Settings(BaseSettings):
    """환경변수(.env)에서 읽는 설정. 비밀값은 SecretStr라 repr·로그에 값이 찍히지 않는다."""

    model_config = SettingsConfigDict(
        env_file=ROOT_ENV_FILE,
        env_file_encoding="utf-8",
        env_ignore_empty=True,  # .env 의 "KEY=" 는 미설정으로 본다
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://signal:signal@localhost:5433/signal"

    toss_mode: TossMode = TossMode.MOCK
    toss_base_url: str = "https://openapi.tossinvest.com"
    toss_timeout_seconds: float = 10.0
    toss_mock_fixtures_dir: Path = ENGINE_ROOT / "tests" / "fixtures" / "toss"
    toss_client_id: SecretStr | None = None
    toss_client_secret: SecretStr | None = None
    toss_account_seq: str | None = None

    # --- 수집 배치 (3단계) ---
    # 대상 종목: 보통주 + 정상 거래 + 최근 N일 평균 거래대금(종가×거래량) ≥ 기준(원)
    collect_min_avg_trading_value: int = 1_000_000_000
    collect_avg_trading_value_days: int = 20
    # 이미 있는 날짜는 건너뛰되 최근 N거래일은 다시 받아 수정주가·잠정치를 반영한다
    collect_refetch_recent_days: int = 5
    # 처음 받는 종목은 이만큼(달력 기준 일수) 과거부터 받는다
    collect_initial_lookback_days: int = 200
    # 거래대금 조건과 무관하게 항상 받는 종목 (069500 = KODEX 200, 벤치마크)
    collect_always_symbols: list[str] = ["069500"]
    collect_concurrency: int = 4
    # 평일 15:50 KST (분 시 일 월 요일)
    collect_cron: str = "50 15 * * mon-fri"

    # --- 백테스트 (4단계) ---
    # TODO(사용자 확인 필요): 아래 비용 기본값은 추정치다. 실제 수수료는 GET /api/v1/commissions,
    # 증권거래세(농특세 포함)는 연도·시장별로 다르니 확인 후 .env 로 덮어쓸 것.
    backtest_buy_fee_rate: Decimal = Decimal("0.00015")  # 매수 수수료 0.015%
    backtest_sell_fee_rate: Decimal = Decimal("0.00015")  # 매도 수수료 0.015%
    backtest_sell_tax_rate: Decimal = Decimal("0.0020")  # 매도 거래세 0.20% (주식, ETF 는 0)
    backtest_slippage_bps: Decimal = Decimal(10)  # 체결 미끄러짐 편도 0.10%
    backtest_initial_capital: Decimal = Decimal(10_000_000)
    backtest_max_positions: int = 10
    backtest_max_weight: Decimal = Decimal("0.10")  # 종목당 최대 비중
    backtest_benchmark_symbol: str = "069500"  # KODEX 200
    # 기획서 관문: 비용 차감 후 벤치마크 대비 초과수익 > 0, MDD > -20%, 거래 ≥ 100
    gate_min_excess_return: Decimal = Decimal(0)
    gate_mdd_floor: Decimal = Decimal("-0.20")
    gate_min_trades: int = 100

    # pykrx 로 상장폐지 종목을 보충할 때만 필요 (KRX 정보데이터시스템 계정)
    krx_id: str | None = None
    krx_pw: SecretStr | None = None

    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    anthropic_api_key: SecretStr | None = None
    anthropic_model: str | None = None

    timezone: str = "Asia/Seoul"


@lru_cache
def get_settings() -> Settings:
    return Settings()
