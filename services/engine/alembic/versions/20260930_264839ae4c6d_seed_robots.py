"""seed robots

Revision ID: 264839ae4c6d
Revises: 4c1b16e31441
Create Date: 2026-09-30 13:56:02.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "264839ae4c6d"
down_revision: str | Sequence[str] | None = "4c1b16e31441"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# MVP 에서는 surge_entry, oversold_rebound 두 개만 구현한다(enabled_globally=true).
# params 는 비워 두고 4단계에서 전략 클래스의 기본값을 쓴다(DB 값은 덮어쓰기용).
# TODO(사용자 확인): 나머지 5개의 이름·보유기간·위험도는 기획서 원문 확인 후 조정.
ROBOTS = [
    {
        "id": "surge_entry",
        "name": "수급 초입",
        "description": "거래량이 급증하고 외국인·기관이 함께 사는 종목의 초입을 잡는다.",
        "style": "day_trade",
        "holding_period": "1~3일",
        "risk_level": "high",
        "enabled_globally": True,
    },
    {
        "id": "oversold_rebound",
        "name": "과대낙폭 반등",
        "description": "고점 대비 크게 빠진 뒤 과매도에서 벗어나는 반등을 노린다.",
        "style": "swing",
        "holding_period": "1~4주",
        "risk_level": "medium",
        "enabled_globally": True,
    },
    {
        "id": "pullback_swing",
        "name": "눌림목 스윙",
        "description": "상승 추세 중 일시적으로 눌린 구간에서 진입한다.",
        "style": "swing",
        "holding_period": "1~4주",
        "risk_level": "medium",
        "enabled_globally": False,
    },
    {
        "id": "trend_follow",
        "name": "추세 추종",
        "description": "중장기 이동평균 위에서 추세가 이어지는 종목을 따라간다.",
        "style": "position",
        "holding_period": "1~3개월",
        "risk_level": "medium",
        "enabled_globally": False,
    },
    {
        "id": "value",
        "name": "가치",
        "description": "재무 지표 대비 저평가된 종목을 모아 오래 보유한다.",
        "style": "long_term",
        "holding_period": "6개월 이상",
        "risk_level": "low",
        "enabled_globally": False,
    },
    {
        "id": "dividend",
        "name": "배당",
        "description": "배당이 꾸준하고 수익률이 높은 종목을 보유한다.",
        "style": "long_term",
        "holding_period": "1년 이상",
        "risk_level": "low",
        "enabled_globally": False,
    },
    {
        "id": "etf_allocation",
        "name": "ETF 자산배분",
        "description": "ETF 로 자산을 나눠 담고 주기적으로 비중을 맞춘다.",
        "style": "long_term",
        "holding_period": "분기 리밸런싱",
        "risk_level": "low",
        "enabled_globally": False,
    },
]

robots = sa.table(
    "robots",
    sa.column("id", sa.Text),
    sa.column("name", sa.Text),
    sa.column("description", sa.Text),
    sa.column("style", sa.Text),
    sa.column("holding_period", sa.Text),
    sa.column("risk_level", sa.Text),
    sa.column("params", JSONB),
    sa.column("enabled_globally", sa.Boolean),
)

robot_settings = sa.table(
    "robot_settings",
    sa.column("robot_id", sa.Text),
    sa.column("is_active", sa.Boolean),
)


def upgrade() -> None:
    op.bulk_insert(robots, [{**robot, "params": {}} for robot in ROBOTS])
    op.bulk_insert(robot_settings, [{"robot_id": r["id"], "is_active": False} for r in ROBOTS])


def downgrade() -> None:
    ids = [r["id"] for r in ROBOTS]
    op.execute(robot_settings.delete().where(robot_settings.c.robot_id.in_(ids)))
    op.execute(robots.delete().where(robots.c.id.in_(ids)))
