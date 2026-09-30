import traceback

import httpx
import pytest


async def test_external_network_is_blocked_in_tests() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(Exception) as exc:  # noqa: B017 - httpx 가 ExceptionGroup 으로 감쌀 수 있다
            await client.get("https://openapi.tossinvest.com/api/v1/prices?symbols=005930")

    assert "외부 네트워크" in "".join(traceback.format_exception(exc.value))
