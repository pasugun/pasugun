import socket

import pytest

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
_real_connect = socket.socket.connect


def _guarded_connect(self: socket.socket, address: object) -> None:
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, str) and host not in _LOCAL_HOSTS and self.family != socket.AF_UNIX:
        raise RuntimeError(f"테스트에서 외부 네트워크 연결을 시도했습니다: {address!r}")
    _real_connect(self, address)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _block_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """테스트가 실제 토스 API 등 외부로 나가지 못하게 막는다(로컬 DB 는 허용)."""
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
