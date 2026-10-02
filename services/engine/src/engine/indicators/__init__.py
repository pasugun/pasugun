"""기술적 지표 순수 함수.

모든 함수는 입력과 같은 길이의 리스트를 돌려주고, 계산할 수 없는 앞부분(워밍업)은 None 이다.
인과적(causal)이다: 결과의 i 번째 값은 values[: i + 1] 만으로 결정된다.
그래서 전체 시계열로 한 번 계산해 두고 t 시점 값만 읽어도 미래 데이터를 쓰지 않는다(테스트로 보장).

가격 계산은 Decimal 로 한다.
"""

from collections import deque
from collections.abc import Sequence
from decimal import Decimal

Series = list[Decimal | None]

ZERO = Decimal(0)
HUNDRED = Decimal(100)


def _check_window(n: int) -> None:
    if n < 1:
        raise ValueError(f"window 는 1 이상이어야 합니다: {n}")


def sma(values: Sequence[Decimal], n: int) -> Series:
    """단순 이동평균. i 번째 = values[i-n+1 .. i] 평균."""
    _check_window(n)
    out: Series = []
    total = ZERO
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        out.append(total / n if i >= n - 1 else None)
    return out


def rolling_max(values: Sequence[Decimal], n: int) -> Series:
    """values[i-n+1 .. i] 의 최댓값."""
    return _rolling_extreme(values, n, take_max=True)


def rolling_min(values: Sequence[Decimal], n: int) -> Series:
    """values[i-n+1 .. i] 의 최솟값."""
    return _rolling_extreme(values, n, take_max=False)


def _rolling_extreme(values: Sequence[Decimal], n: int, *, take_max: bool) -> Series:
    _check_window(n)
    out: Series = []
    window: deque[int] = deque()  # 단조 덱: 후보 인덱스
    for i, v in enumerate(values):
        while window and (values[window[-1]] <= v if take_max else values[window[-1]] >= v):
            window.pop()
        window.append(i)
        if window[0] <= i - n:
            window.popleft()
        out.append(values[window[0]] if i >= n - 1 else None)
    return out


def shift(values: Sequence[Decimal | None], k: int = 1) -> Series:
    """k 칸 뒤로 민다. i 번째 = values[i-k] (앞 k 개는 None). 예: '직전 20일 평균'."""
    if k < 0:
        raise ValueError("미래 쪽으로는 밀 수 없습니다(k < 0)")
    return [None] * min(k, len(values)) + list(values[: max(0, len(values) - k)])


def rsi(closes: Sequence[Decimal], n: int = 14) -> Series:
    """Wilder RSI.

    - 첫 값(i = n): 처음 n 개 변화량의 단순 평균 상승폭·하락폭
    - 이후: avg = (이전 avg × (n-1) + 이번 변화) / n
    - RSI = 100 - 100 / (1 + 평균상승/평균하락). 하락이 0 이면 100, 상승·하락 모두 0 이면 50.
    """
    _check_window(n)
    out: Series = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(closes[i] - closes[i - 1], ZERO) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], ZERO) for i in range(1, len(closes))]
    avg_gain = sum(gains[:n], ZERO) / n
    avg_loss = sum(losses[:n], ZERO) / n
    out[n] = _rsi_value(avg_gain, avg_loss)
    for i in range(n + 1, len(closes)):
        avg_gain = (avg_gain * (n - 1) + gains[i - 1]) / n
        avg_loss = (avg_loss * (n - 1) + losses[i - 1]) / n
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: Decimal, avg_loss: Decimal) -> Decimal:
    if avg_loss == 0:
        return Decimal(50) if avg_gain == 0 else HUNDRED
    return HUNDRED - HUNDRED / (1 + avg_gain / avg_loss)


def stdev(values: Sequence[Decimal], n: int) -> Series:
    """모표준편차(ddof=0). 볼린저밴드 관례."""
    _check_window(n)
    means = sma(values, n)
    out: Series = []
    for i, mean in enumerate(means):
        if mean is None:
            out.append(None)
            continue
        window = values[i - n + 1 : i + 1]
        variance = sum(((v - mean) ** 2 for v in window), ZERO) / n
        out.append(variance.sqrt())
    return out


def bollinger(
    closes: Sequence[Decimal], n: int = 20, k: Decimal = Decimal(2)
) -> tuple[Series, Series, Series]:
    """(중심선, 상단, 하단) = (SMA n, SMA + k·σ, SMA - k·σ)."""
    mid = sma(closes, n)
    sd = stdev(closes, n)
    upper: Series = [
        m + k * s if m is not None and s is not None else None for m, s in zip(mid, sd, strict=True)
    ]
    lower: Series = [
        m - k * s if m is not None and s is not None else None for m, s in zip(mid, sd, strict=True)
    ]
    return mid, upper, lower


def multiply(a: Sequence[Decimal], b: Sequence[Decimal]) -> list[Decimal]:
    """원소별 곱. 예: 종가 × 거래량 = 거래대금 근사."""
    if len(a) != len(b):
        raise ValueError("길이가 다릅니다")
    return [x * y for x, y in zip(a, b, strict=True)]
