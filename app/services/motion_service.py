"""포인터 움직임 요약을 받아 남긴다. 저장 실패가 본래 기능을 막지 않는다.

이 값은 봇 판별을 **나중에** 하기 위한 재료다. 지금은 판정에 쓰지 않는다 — 정상
사용자의 분포를 먼저 알아야 기준을 정할 수 있고, 그 순서를 안 지켜서 로그인 캡차에서
한 번 겪었다(사람 10명으로 기준을 정하려다 승격 기준을 못 넘겼다).

그래서 여기서 나는 오류는 전부 삼킨다. 로그인·강의 시청·시험 제출이 **관측 때문에
실패하는 일은 없어야 한다.** 관측은 부수적인 일이고 본래 기능이 우선이다.
"""

from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import MotionSample
from app.schemas.motion import MotionIn

logger = logging.getLogger("catchap.motion")


def record(
    db: Session,
    motion: MotionIn | None,
    *,
    surface: str,
    subject_id: str | None = None,
    context_id: str | None = None,
) -> None:
    """한 줄 남긴다. 조용히 실패한다 — 호출부의 흐름을 절대 바꾸지 않는다.

    `commit` 은 하지 않는다. 호출부가 이미 트랜잭션을 들고 있어서, 여기서 커밋하면
    아직 끝나지 않은 작업을 함께 확정해버린다.
    """
    if motion is None:
        return
    # 설정으로 멈출 수 있어야 한다. 강의를 보는 내내 관측하는 일이라 개인정보
    # 처리방침·고지 여부가 정해지기 전에 멈춰야 할 수도 있는데, 그때 코드를 되돌리는
    # 것은 과하다. 프론트는 계속 보내되 여기서 버린다.
    if get_settings().MOTION_COLLECT_MODE != "record":
        return
    # 움직임이 아예 없는 구간은 남기지 않는다. 강의를 집중해서 보는 사람이 대부분
    # 여기 해당해서, 남기면 표의 대부분이 0 으로 채워지고 분포를 볼 때 방해가 된다.
    # "안 움직였다" 는 사실 자체는 판정에 쓰지 않기로 했으므로 잃는 것이 없다.
    if motion.n <= 0:
        return
    try:
        db.add(
            MotionSample(
                surface=surface,
                subject_id=subject_id,
                context_id=context_id,
                n=motion.n,
                dist=motion.dist,
                span=motion.span,
                turns=motion.turns,
                micro=motion.micro,
                pauses=motion.pauses,
                gaps=motion.gaps,
            )
        )
        db.flush()
    except SQLAlchemyError:
        # 관측이 본래 기능을 막지 않는다. 다만 조용히 사라지면 "쌓이는 줄 알았는데
        # 아니었다" 가 되므로 로그는 남긴다.
        logger.warning("motion 기록 실패 — surface=%s", surface, exc_info=True)
