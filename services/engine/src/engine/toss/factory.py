"""설정(TOSS_MODE)에 따라 실제 또는 mock 클라이언트를 만든다."""

import httpx

from engine.clock import now_kst
from engine.config import Settings, TossMode, get_settings
from engine.db.session import make_async_engine
from engine.toss.api import TossApi
from engine.toss.client import TossClient
from engine.toss.mock import MockTossClient
from engine.toss.rate_limit import RateLimiter
from engine.toss.token_manager import DbTokenStore, TokenManager, http_token_issuer


def create_toss_api(settings: Settings | None = None) -> TossApi:
    settings = settings or get_settings()
    if settings.toss_mode is TossMode.MOCK:
        return MockTossClient(settings.toss_mock_fixtures_dir)

    if settings.toss_client_id is None or settings.toss_client_secret is None:
        raise RuntimeError("TOSS_MODE=live 에는 TOSS_CLIENT_ID, TOSS_CLIENT_SECRET 이 필요합니다")

    http = httpx.AsyncClient(base_url=settings.toss_base_url, timeout=settings.toss_timeout_seconds)
    limiter = RateLimiter()
    token_manager = TokenManager(
        DbTokenStore(make_async_engine()),
        http_token_issuer(
            http,
            settings.toss_client_id.get_secret_value(),
            settings.toss_client_secret.get_secret_value(),
            limiter,
        ),
        now=now_kst,
    )
    return TossClient(http, token_manager, limiter, account_seq=settings.toss_account_seq)
