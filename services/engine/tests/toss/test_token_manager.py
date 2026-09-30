import asyncio
import logging
from datetime import timedelta

from engine.toss.token_manager import InMemoryTokenStore, StoredToken, TokenManager
from tests.toss.helpers import NOW, FakeIssuer, valid_token


def manager(store: InMemoryTokenStore, issuer: FakeIssuer) -> TokenManager:
    return TokenManager(store, issuer, now=lambda: NOW)


async def test_issues_token_when_store_is_empty_and_saves_it() -> None:
    store, issuer = InMemoryTokenStore(), FakeIssuer(expires_in=3600)

    token = await manager(store, issuer).get_token()

    assert token == "tok-1"
    assert issuer.calls == 1
    assert store.token == StoredToken("tok-1", NOW + timedelta(seconds=3600))


async def test_reuses_valid_stored_token_without_issuing() -> None:
    store, issuer = InMemoryTokenStore(valid_token("shared")), FakeIssuer()

    assert await manager(store, issuer).get_token() == "shared"
    assert issuer.calls == 0


async def test_refreshes_token_that_expires_within_margin() -> None:
    store = InMemoryTokenStore(StoredToken("old", NOW + timedelta(minutes=4)))
    issuer = FakeIssuer()

    assert await manager(store, issuer).get_token() == "tok-1"
    assert issuer.calls == 1


async def test_concurrent_get_token_issues_only_once() -> None:
    store, issuer = InMemoryTokenStore(), FakeIssuer()
    tm = manager(store, issuer)

    tokens = await asyncio.gather(*(tm.get_token() for _ in range(10)))

    assert set(tokens) == {"tok-1"}
    assert issuer.calls == 1


async def test_two_processes_sharing_store_issue_only_once() -> None:
    """프로세스 A, B 가 같은 DB(저장소)를 보고 동시에 만료 토큰을 갱신하려는 상황."""
    store = InMemoryTokenStore(StoredToken("expired", NOW - timedelta(seconds=1)))
    issuer = FakeIssuer()
    process_a, process_b = manager(store, issuer), manager(store, issuer)

    a, b = await asyncio.gather(process_a.get_token(), process_b.get_token())

    assert a == b == "tok-1"
    assert issuer.calls == 1


async def test_refresh_reuses_token_already_replaced_by_other_process() -> None:
    """A 가 401 token-revoked 를 받았지만 B 가 이미 새 토큰을 저장해 둔 경우 재발급하지 않는다."""
    store, issuer = InMemoryTokenStore(valid_token("from-b")), FakeIssuer()

    token = await manager(store, issuer).refresh(stale_token="from-a")

    assert token == "from-b"
    assert issuer.calls == 0


async def test_refresh_issues_when_stored_token_is_the_stale_one() -> None:
    store, issuer = InMemoryTokenStore(valid_token("revoked")), FakeIssuer()

    assert await manager(store, issuer).refresh(stale_token="revoked") == "tok-1"
    assert issuer.calls == 1


async def test_token_value_is_never_logged(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    store, issuer = InMemoryTokenStore(), FakeIssuer()

    await manager(store, issuer).get_token()

    assert "tok-1" not in caplog.text
    assert "tok-1" not in repr(store.token)
    assert "토큰을 새로 발급" in caplog.text
