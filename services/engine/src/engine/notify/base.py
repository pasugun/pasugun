"""알림 채널 인터페이스. 텔레그램 구현은 5단계에서 붙인다."""

import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    async def send(self, text: str) -> None: ...


class LogNotifier:
    """알림 채널이 아직 없을 때 로그로만 남긴다."""

    async def send(self, text: str) -> None:
        logger.warning("[알림] %s", text)


class RecordingNotifier:
    """테스트용: 보낸 메시지를 모아 둔다."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, text: str) -> None:
        self.messages.append(text)
