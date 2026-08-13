from pydantic import BaseModel, Field

# 프론트가 40ms 마다 한 점씩 모으므로 10초 구간이면 250개가 상한이다. 그보다 큰 값은
# 우리가 보낸 것이 아니다 — 막되 요청 자체를 실패시키지는 않는다(`motion_service.record`).
MAX_SAMPLES = 5000


class MotionIn(BaseModel):
    """브라우저가 보내는 포인터 움직임 요약. **좌표는 이 스키마에 아예 없다.**

    왜 요약만 받나 — 마우스 궤적은 그것만으로 사람이 구분된다(2026-08-12 실측: 같은
    사람 4명 전원 식별, 다른 사람 오인 0.040%). 40분 강의를 40ms 마다 받으면 1.8MB 고
    하루 수백만 행이다. 브라우저가 세고 숫자만 보내면 두 문제가 같이 사라진다.
    (`catchap-frontend` `lib/motionSummary.ts` 가 만드는 값이다)

    필드가 하나라도 범위를 벗어나면 그 요청은 무시된다 — 관측 때문에 로그인·시청·시험이
    실패하는 일은 없어야 한다.
    """

    # 표본 수. 0 이면 그 구간에 움직임이 없었다는 뜻이고 그 자체는 판단하지 않는다 —
    # 강의를 집중해서 보는 사람이 정확히 그렇게 행동한다.
    n: int = Field(ge=0, le=MAX_SAMPLES)
    # 이동 거리 합과 시작-끝 직선 거리 (둘 다 화면 비율). span/dist 가 곧음의 척도.
    dist: float = Field(ge=0)
    span: float = Field(ge=0)
    turns: int = Field(ge=0, le=MAX_SAMPLES)
    micro: float = Field(ge=0, le=1)
    pauses: int = Field(ge=0, le=MAX_SAMPLES)
    # 표본 간격의 변동계수. 기계는 0 에 가깝다.
    gaps: float = Field(ge=0)
