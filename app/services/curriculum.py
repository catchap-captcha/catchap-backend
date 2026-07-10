"""교육 커리큘럼 — 매일 주제가 바뀌는 일일 과제 스케줄.

규칙(사용자 결정 2026-07-07):
- 하루 = 한 주제 = 25문제(5단계 × 5). 매일 순서대로 다음 주제로 바뀐다.
- 지난 날(<오늘)은 언제든 다시 풀 수 있다(복습 — 코인·상태 반영 없음).
- 다음 날(>오늘)은 풀 수 없지만 '주제(커리큘럼)'는 미리 볼 수 있다.
- 오늘 것만 정식 과제(코인·오늘의퀴즈 완료 반영).

일차 계산은 고정 기준일(anchor)로 결정적. 주제는 순환(주제 소진 시 처음으로).
"""

from datetime import date, timedelta

from app.services import life_bank, subject_banks

# 커리큘럼 시작 기준일. 10주제가 순환하므로 이 날은 위상(phase) 기준.
# 데모 편의상 '오늘'이 플레이 가능한 첫 주제(교통안전)에 오도록 맞춤 (지난 10일 복습 가능).
# 실제 운영 시 개학일로 교체.
ANCHOR = date(2026, 6, 27)

# 생활 교육과정: ms 생활안전 주제 순서 (하루 1주제). 순환한다.
LIFE_SEQUENCE = [
    "교통안전", "화재안전", "손씻기", "우리집 안전", "긴급 전화",
    "디지털 안전", "날씨 안전", "놀이터 안전", "전기 안전", "미아 안전",
]

STAGES = 5
PER_STAGE = 5


def today_index(today: date | None = None) -> int:
    """기준일로부터 오늘이 며칠차인지 (0-base). 개학 전이면 0으로 clamp."""
    today = today or date.today()
    return max(0, (today - ANCHOR).days)


def topic_for_index(idx: int) -> str:
    return LIFE_SEQUENCE[idx % len(LIFE_SEQUENCE)]


def date_for_index(idx: int) -> date:
    return ANCHOR + timedelta(days=idx)


# 주제→문항 인덱스는 임포트 시 1회 구축 — curriculum_window가 요청당 창 길이(약 11일)만큼
# 호출하므로 매번 은행 전체(275문항)를 재스캔하지 않는다. 반환 리스트는 공유 객체(수정 금지).
_TOPIC_QUESTIONS: dict[str, list[dict]] = {}
for _q in life_bank.LIFE_FULL:
    _TOPIC_QUESTIONS.setdefault(_q["topic"], []).append(_q)


def _topic_questions(topic: str) -> list[dict]:
    return _TOPIC_QUESTIONS.get(topic, [])


def day_status(idx: int, today_idx: int) -> str:
    if idx < today_idx:
        return "past"
    if idx == today_idx:
        return "today"
    return "future"


def curriculum_window(subject: str, back: int, forward: int, today: date | None = None) -> dict:
    """오늘 기준 앞뒤 일자 목록 (지난날=복습 가능, 오늘=과제, 미래=주제만 잠금)."""
    tix = today_index(today)
    start = max(0, tix - back)
    days = []
    for idx in range(start, tix + forward + 1):
        topic = topic_for_index(idx)
        qs = _topic_questions(topic)
        playable = sum(1 for q in qs if q["playable"])
        st = day_status(idx, tix)
        days.append(
            {
                "day": idx + 1,  # 사람이 보는 1-base
                "index": idx,
                "date": date_for_index(idx).isoformat(),
                "topic": topic,
                "stages": STAGES,
                "total": len(qs),
                "playable_count": playable,
                "status": st,
                "locked": st == "future",
                "replayable": st == "past" and playable > 0,
            }
        )
    return {"subject": subject, "today_index": tix, "today_day": tix + 1, "days": days}


def day_detail(subject: str, day: int, today: date | None = None) -> dict:
    """특정 일차 상세. 미래 일차면 잠금(주제만), 오늘/지난날이면 문항(단계별) 반환."""
    idx = day - 1
    tix = today_index(today)
    topic = topic_for_index(idx)
    qs = _topic_questions(topic)
    if idx > tix:
        # 다음 날: 못 풀되 커리큘럼(주제·단계 구성)은 확인 가능
        plan = []
        for s in range(1, STAGES + 1):
            types = sorted({q["type"] for q in qs if q["stage"] == s})
            plan.append({"stage": s, "count": PER_STAGE, "types": types})
        return {
            "day": day, "date": date_for_index(idx).isoformat(), "topic": topic,
            "locked": True, "status": "future", "total": len(qs), "stage_plan": plan,
        }
    # 오늘/지난날: 단계별 문항 (정답 제거). 현재 UI로는 playable(single)만 실제 플레이.
    stages = []
    for s in range(1, STAGES + 1):
        s_qs = [subject_banks.public_question(q) for q in qs if q["stage"] == s]
        stages.append({"stage": s, "questions": s_qs})
    playable = [subject_banks.public_question(q) for q in qs if q["playable"]]
    return {
        "day": day, "date": date_for_index(idx).isoformat(), "topic": topic,
        "locked": False, "status": day_status(idx, tix),
        "is_replay": idx < tix, "total": len(qs),
        "playable_count": len(playable), "stages": stages, "playable": playable,
    }
