"""과목별 문제은행 레지스트리 — 실전 게임(game-session/game-answer)의 단일 진입점.

과목 추가 시 여기 BANKS에만 등록하면 game-session 발급·서버 채점·오답노트·행동데이터가
같은 파이프라인을 탄다. 정답(answer)·해설(explain)은 서버에만 존재한다.
- 생활: capcha_service ms 브랜치 (커리큘럼 일차 순환은 생활 전용 — curriculum.py)
- 수학·과학: capcha_service my 브랜치 / 사회: capcha_service sw 브랜치(02-history) → ms social 예정
- 국어: capcha_service jy 브랜치 (4학년 국어 13유형)
"""

from app.services import (
    english_bank,
    english_listen,
    english_trace,
    social_bank,
    korean_bank,
    life_bank,
    math_bank,
    science_bank,
)

BANKS: dict[str, list[dict]] = {
    "국어": korean_bank.KOREAN_FULL,
    "생활": life_bank.LIFE_FULL,
    "수학": math_bank.MATH_FULL,
    "과학": science_bank.SCIENCE_FULL,
    "사회": social_bank.SOCIAL_FULL,
    # 영어 = 문법·그림문장·생성기(텍스트) + 듣기(오디오) + 알파벳 따라쓰기(trace)
    "영어": english_bank.ENGLISH_FULL + english_listen.ENGLISH_LISTEN + english_trace.ENGLISH_TRACE,
}

# 실전(서버 채점) 지원 과목 — 나머지 과목은 game-session available=false(프론트 데모 유지)
LIVE_SUBJECTS = frozenset(BANKS)

# 오답노트 카테고리(D.WRONG_TAGS 키) 매핑
WRONG_CATEGORY = {
    "국어": "word", "생활": "safe", "수학": "num", "과학": "img", "사회": "soc", "영어": "eng",
}

_BY_ID: dict[str, dict[str, dict]] = {
    subject: {q["id"]: q for q in bank} for subject, bank in BANKS.items()
}

# playable 풀도 임포트 시 1회 구축 — 챕터 API가 요청당 수십 번 호출하므로 매번 은행
# 전체를 재스캔하지 않는다. 반환 리스트는 공유 객체이니 호출부에서 수정 금지(슬라이싱만).
_PLAYABLE: dict[str, list[dict]] = {
    subject: [q for q in bank if q["playable"]] for subject, bank in BANKS.items()
}


def get_question(subject: str, qid: str) -> dict | None:
    """과목 스코프 문항 조회 — 타 과목 문항 id로 교차 제출하는 위조를 차단한다."""
    return _BY_ID.get(subject, {}).get(qid)


def playable_pool(subject: str) -> list[dict]:
    return _PLAYABLE.get(subject, [])


# 조작형 렌더 필드 — 정답(answer)이 아닌 표시 데이터만. right/cards/items는 추출 단계에서
# 이미 셔플되어 정답 순서를 노출하지 않는다. answer/explain/playable(정답 id)은 절대 미포함.
_RENDER_FIELDS = ("options", "left", "right", "bins", "items", "cards", "zones",
                  "reference", "mapStyle", "compass", "start", "layout", "audio",
                  "template", "glyph", "character", "dest", "dangers",
                  "flag", "cols", "rows", "slots", "pieces",
                  # 원본 유형 복원 렌더 필드 — 정답 미포함인 것만.
                  # (dictation의 tts=정답 문장, swipe의 statements=태그 포함 → 여기 제외.
                  #  챌린지 발급 경로(captcha_service)만 tts를 내려준다.)
                  "tokens", "gaps", "markLabel", "before", "highlight", "after",
                  "size", "words", "tiles", "level", "reveal", "target", "scene_svg", "regions")


def public_question(q: dict) -> dict:
    """프론트로 내려줄 형태 — 정답(answer)·해설(explain) 제거.

    playable은 bool로 변환한다(원본은 정답 옵션 id를 담고 있어 그대로 내리면 정답 유출).
    조작형(connect/sort/order/place)은 유형별 렌더 필드만 노출한다.
    """
    pub = {
        "id": q["id"], "topic": q["topic"], "stage": q["stage"], "type": q["type"],
        "prompt": q["prompt"], "hint": q["hint"], "playable": bool(q["playable"]),
    }
    for f in _RENDER_FIELDS:
        if f in q:
            pub[f] = q[f]
    return pub
