from typing import Any

from engine.robots.base import Strategy
from engine.robots.oversold_rebound import OversoldRebound
from engine.robots.surge_entry import SurgeEntry

# MVP 에서 구현한 로봇. 나머지(robots 테이블의 enabled_globally=false)는 아직 없다.
STRATEGIES: dict[str, type[Strategy[Any]]] = {cls.id: cls for cls in (SurgeEntry, OversoldRebound)}


def get_strategy(robot_id: str, overrides: dict[str, Any] | None = None) -> Strategy[Any]:
    try:
        cls = STRATEGIES[robot_id]
    except KeyError:
        raise KeyError(
            f"구현되지 않은 로봇입니다: {robot_id} (가능: {', '.join(STRATEGIES)})"
        ) from None
    return cls.from_overrides(overrides)
