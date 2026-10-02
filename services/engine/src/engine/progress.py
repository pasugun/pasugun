import sys
import time


class ProgressPrinter:
    """진행률을 stderr 에 찍는다. 너무 자주 찍지 않도록 0.5초 간격(마지막은 항상)."""

    def __init__(self, label: str, unit: str = "행") -> None:
        self.label = label
        self.unit = unit
        self.started = time.monotonic()
        self._last = 0.0

    def __call__(self, done: int, total: int, symbol: str, rows: int | None) -> None:
        now = time.monotonic()
        if done != total and now - self._last < 0.5:
            return
        self._last = now
        elapsed = now - self.started
        eta = elapsed / done * (total - done) if done else 0
        status = "실패" if rows is None else f"+{rows}{self.unit}"
        print(
            f"[{self.label}] {done}/{total} ({done / total:.0%}) {symbol} {status} "
            f"경과 {elapsed:.0f}s 남은 {eta:.0f}s",
            file=sys.stderr,
        )
