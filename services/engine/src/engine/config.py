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
