"""지표 테스트. 기대값은 모두 손으로 계산한 값이다."""

from decimal import Decimal

import pytest

from engine.indicators import (
    bollinger,
    multiply,
    rolling_max,
    rolling_min,
    rsi,
    shift,
    sma,
    stdev,
)


def D(*xs: float | int | str) -> list[Decimal]:
    return [Decimal(str(x)) for x in xs]


def test_sma() -> None:
    # [1,2,3,4,5], n=3 → None, None, (1+2+3)/3=2, 3, 4
    assert sma(D(1, 2, 3, 4, 5), 3) == [None, None, Decimal(2), Decimal(3), Decimal(4)]


def test_sma_window_1_is_identity() -> None:
    assert sma(D(7, 8), 1) == D(7, 8)


def test_rolling_max_min() -> None:
    values = D(3, 1, 4, 1, 5, 9, 2)
    # n=3: [3,1,4]→4, [1,4,1]→4, [4,1,5]→5, [1,5,9]→9, [5,9,2]→9
    assert rolling_max(values, 3) == [None, None, *D(4, 4, 5, 9, 9)]
    # n=3: 1, 1, 1, 1, 2
    assert rolling_min(values, 3) == [None, None, *D(1, 1, 1, 1, 2)]


def test_shift_moves_values_back_and_refuses_future() -> None:
    assert shift(D(1, 2, 3), 1) == [None, Decimal(1), Decimal(2)]
    assert shift(D(1, 2), 5) == [None, None]
    with pytest.raises(ValueError):
        shift(D(1, 2), -1)


def test_stdev_population() -> None:
    # [2,4,4,4,5,5,7,9] 평균 5, 편차제곱합 32, /8 = 4 → σ = 2
    assert stdev(D(2, 4, 4, 4, 5, 5, 7, 9), 8)[-1] == Decimal(2)


def test_bollinger() -> None:
    mid, upper, lower = bollinger(D(2, 4, 4, 4, 5, 5, 7, 9), n=8, k=Decimal(2))
    assert (mid[-1], upper[-1], lower[-1]) == (Decimal(5), Decimal(9), Decimal(1))
    assert mid[:-1] == [None] * 7


def test_rsi_hand_calculated_n3() -> None:
    """n=3 으로 손계산.

    closes = 10, 11, 12, 11, 13, 12
    변화      +1, +1, -1, +2, -1
    i=3: 처음 3개 변화(+1,+1,-1) → 평균상승 2/3, 평균하락 1/3, RS=2 → RSI = 100 - 100/3 = 66.666…
    i=4: 상승 (2/3·2 + 2)/3 = 10/9, 하락 (1/3·2 + 0)/3 = 2/9, RS=5 → RSI = 100 - 100/6 = 83.333…
    i=5: 상승 (10/9·2 + 0)/3 = 20/27, 하락 (2/9·2 + 1)/3 = 13/27, RS=20/13
         → 100 - 100·13/33 = 60.6060…
    """
    out = rsi(D(10, 11, 12, 11, 13, 12), 3)
    assert out[:3] == [None, None, None]
    assert out[3] == pytest.approx(Decimal(200) / 3)
    assert out[4] == pytest.approx(Decimal(500) / 6)
    assert out[5] == pytest.approx(Decimal(100) - Decimal(1300) / 33)


def test_rsi_edges() -> None:
    assert rsi(D(1, 2, 3, 4), 3)[-1] == Decimal(100)  # 하락 없음
    assert rsi(D(5, 5, 5, 5), 3)[-1] == Decimal(50)  # 변화 없음
    assert rsi(D(4, 3, 2, 1), 3)[-1] == Decimal(0)  # 상승 없음
    assert rsi(D(1, 2, 3), 3) == [None, None, None]  # 데이터 부족


def test_multiply() -> None:
    assert multiply(D(2, 3), D(10, 100)) == D(20, 300)


SERIES = D(*[100 + ((i * 37) % 23) - 11 for i in range(80)])


@pytest.mark.parametrize(
    "fn",
    [
        lambda v: sma(v, 20),
        lambda v: rolling_max(v, 60),
        lambda v: rolling_min(v, 20),
        lambda v: rsi(v, 14),
        lambda v: stdev(v, 20),
        lambda v: bollinger(v, 20)[2],
        lambda v: shift(sma(v, 20), 1),
    ],
)
def test_indicators_are_causal(fn) -> None:
    """앞부분만으로 계산한 값 == 전체로 계산한 값의 앞부분 (미래 데이터 미사용)."""
    full = fn(SERIES)
    for cut in (1, 15, 21, 40, 61, 79):
        assert fn(SERIES[:cut]) == full[:cut]


def test_invalid_window() -> None:
    with pytest.raises(ValueError):
        sma(D(1), 0)
