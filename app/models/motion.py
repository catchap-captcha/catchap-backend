from sqlalchemy import CHAR, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk


class MotionSample(Base, UUIDPk, Timestamps):
    """포인터 움직임 요약 — 봇 판별용. **좌표는 저장하지 않는다.**

    왜 요약만 남기나
    ----------------
    지금 궤적 분석은 캡차가 떠 있는 몇 초 동안만 돈다. 강의는 40분을 머무는데 그동안
    아무것도 안 보고 있고, 거기가 시청 시뮬레이션 봇이 노는 자리다.

    그렇다고 좌표를 다 받으면 40분에 1.8MB 이고 하루 수백만 행이 된다. 더 큰 이유는
    따로 있다 — 마우스 궤적은 그것만으로 사람이 구분된다(2026-08-12 실측: 같은 사람
    4명 전원 식별, 다른 사람 오인 0.040%). 사이트 전체에서 원본 좌표를 모으는 것은
    다루기 어려운 자료를 만드는 일이다.

    그래서 브라우저가 세고 숫자만 보낸다(`catchap-frontend` `lib/motionSummary.ts`).
    한 줄이 40분 강의의 10초 구간 하나, 또는 로그인·시험 한 번이다.

    무엇을 세는가
    -------------
    2026-08-13 에 사람과 짧은-궤적 봇을 실제로 가른 특징에서 골랐다.

        turns   방향 전환      AUC 0.87   사람 손은 계속 미세하게 꺾인다
        micro   미세 이동 비율  AUC 0.83   기계는 필요한 만큼만 움직인다
        gaps    간격 불규칙     AUC 0.86   기계는 일정한 주기로 움직인다

    무동작(`n = 0`)은 봇 신호가 **아니다**. 강의를 집중해서 보는 사람이 정확히 그렇게
    행동한다. 그걸 벌하면 성실한 사용자만 걸린다. 판정은 움직임이 있었을 때만 한다.

    지금은 기록만 한다
    ------------------
    정상 사용자의 분포를 먼저 알아야 기준을 정할 수 있다. 그 순서를 안 지켜서 로그인
    캡차에서 한 번 겪었다 — 사람 10명으로 기준을 정하려다 승격 기준을 못 넘겼다.
    """

    __tablename__ = "motion_samples"

    # 어느 화면인가 — lecture · login · exam
    surface: Mapped[str] = mapped_column(String(16), index=True)
    # 누구인가. 로그인은 인증 **전**이라 비어 있다 — 그 화면은 분포만 본다.
    # 사람별로 묶어야 "이 사용자가 계속 이상한가" 를 볼 수 있어서 남긴다.
    subject_id: Mapped[str | None] = mapped_column(CHAR(36), index=True, nullable=True)
    # 무엇에 대한 것인가 — 강의 id · 시험 회차 id 등. 화면마다 뜻이 다르다.
    context_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)

    # 표본 수. 0 이면 그 구간에 움직임이 없었다는 뜻이고 그 자체는 판단하지 않는다.
    n: Mapped[int] = mapped_column(Integer, default=0)
    # 이동 거리 합과 시작-끝 직선 거리 (둘 다 화면 비율). span/dist 가 곧음의 척도.
    dist: Mapped[float] = mapped_column(Float, default=0.0)
    span: Mapped[float] = mapped_column(Float, default=0.0)
    turns: Mapped[int] = mapped_column(Integer, default=0)
    micro: Mapped[float] = mapped_column(Float, default=0.0)
    pauses: Mapped[int] = mapped_column(Integer, default=0)
    # 표본 간격의 변동계수. 기계는 0 에 가깝다.
    gaps: Mapped[float] = mapped_column(Float, default=0.0)
