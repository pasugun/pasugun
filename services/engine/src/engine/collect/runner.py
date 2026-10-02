"""종목별 작업을 동시에 몇 개씩 돌리고 결과를 모은다.

API 호출은 동시에 진행하되 DB 세션은 하나라서, 저장소 접근은 db_lock 으로 직렬화한다.
한 종목이 실패해도 나머지는 계속한다.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

import httpx

from engine.toss.errors import TossApiError

logger = logging.getLogger(__name__)

# (완료 수, 전체 수, 종목, 저장 행 수 또는 None=실패)
Progress = Callable[[int, int, str, int | None], None]


@dataclass
class SyncResult:
    job: str
    symbols: int = 0
    rows: int = 0
    failed: dict[str, str] = field(default_factory=dict)
    notes: dict[str, list[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.failed

    def note(self, key: str, symbol: str) -> None:
        self.notes.setdefault(key, []).append(symbol)

    def summary(self) -> dict[str, object]:
        return {
            "job": self.job,
            "symbols": self.symbols,
            "rows": self.rows,
            "failed": dict(list(self.failed.items())[:50]),
            "failed_count": len(self.failed),
            "notes": {k: v[:50] for k, v in self.notes.items()},
        }


async def run_per_symbol(
    job: str,
    symbols: Sequence[str],
    work: Callable[[str, SyncResult], Awaitable[int]],
    *,
    concurrency: int,
    progress: Progress | None = None,
) -> SyncResult:
    result = SyncResult(job=job, symbols=len(symbols))
    semaphore = asyncio.Semaphore(max(1, concurrency))
    done = 0

    async def one(symbol: str) -> None:
        nonlocal done
        async with semaphore:
            try:
                rows: int | None = await work(symbol, result)
                result.rows += rows
            except (TossApiError, httpx.HTTPError) as exc:
                rows = None
                result.failed[symbol] = str(exc)
                logger.warning("%s %s 실패: %s", job, symbol, exc)
            done += 1
            if progress is not None:
                progress(done, len(symbols), symbol, rows)

    await asyncio.gather(*(one(s) for s in symbols))
    return result
