"""과목별 문제은행 레지스트리 — 실전 게임(game-session/game-answer)의 단일 진입점.

과목 추가 시 여기 BANKS에만 등록하면 game-session 발급·서버 채점·오답노트·행동데이터가
같은 파이프라인을 탄다. 정답(answer)·해설(explain)은 서버에만 존재한다.
- 생활: capcha_service ms 브랜치 (커리큘럼 일차 순환은 생활 전용 — curriculum.py)
- 수학·과학: capcha_service my 브랜치 / 역사: capcha_service sw 브랜치(02-history)
"""

from app.services import english_bank, history_bank, life_bank, math_bank, science_bank

BANKS: dict[str, list[dict]] = {
    "생활": life_bank.LIFE_FULL,
    "수학": math_bank.MATH_FULL,
    "과학": science_bank.SCIENCE_FULL,
    "역사": history_bank.HISTORY_FULL,
    "영어": english_bank.ENGLISH_FULL,
}

# 실전(서버 채점) 지원 과목 — 나머지 과목은 game-session available=false(프론트 데모 유지)
LIVE_SUBJECTS = frozenset(BANKS)

# 오답노트 카테고리(D.WRONG_TAGS 키) 매핑
WRONG_CATEGORY = {"생활": "safe", "수학": "num", "과학": "img", "역사": "hist", "영어": "eng"}

_BY_ID: dict[str, dict[str, dict]] = {
    subject: {q["id"]: q for q in bank} for subject, bank in BANKS.items()
}


def get_question(subject: str, qid: str) -> dict | None:
    """과목 스코프 문항 조회 — 타 과목 문항 id로 교차 제출하는 위조를 차단한다."""
    return _BY_ID.get(subject, {}).get(qid)


def playable_pool(subject: str) -> list[dict]:
    return [q for q in BANKS.get(subject, []) if q["playable"]]


def public_question(q: dict) -> dict:
    """프론트로 내려줄 형태 — 정답(answer)·해설(explain) 제거.

    playable은 bool로 변환한다(원본은 정답 옵션 id를 담고 있어 그대로 내리면 정답 유출).
    """
    return {
        "id": q["id"], "topic": q["topic"], "stage": q["stage"], "type": q["type"],
        "prompt": q["prompt"], "hint": q["hint"], "options": q["options"],
        "playable": bool(q["playable"]),
    }
