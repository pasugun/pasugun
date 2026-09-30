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


class TossMode(StrEnum):
    MOCK = "mock"
    LIVE = "live"


class Settings(BaseSettings):
    """환경변수(.env)에서 읽는 설정. 비밀값은 SecretStr라 repr·로그에 값이 찍히지 않는다."""

    model_config = SettingsConfigDict(
        env_file=ROOT_ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql+psycopg://signal:signal@localhost:5433/signal"

    toss_mode: TossMode = TossMode.MOCK
    toss_client_id: SecretStr | None = None
    toss_client_secret: SecretStr | None = None
    toss_account_seq: str | None = None

    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    anthropic_api_key: SecretStr | None = None
    anthropic_model: str | None = None

    timezone: str = "Asia/Seoul"


@lru_cache
def get_settings() -> Settings:
    return Settings()
