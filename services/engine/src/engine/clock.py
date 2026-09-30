from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    """현재 시각(KST). 결정성이 필요한 곳에서는 이 함수를 주입받아 쓴다."""
    return datetime.now(KST)
