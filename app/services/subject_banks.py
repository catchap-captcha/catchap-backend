"""과목별 문제은행 레지스트리 — 실전 게임(game-session/game-answer)의 단일 진입점.

과목 추가 시 여기 BANKS에만 등록하면 game-session 발급·서버 채점·오답노트·행동데이터가
같은 파이프라인을 탄다. 정답(answer)·해설(explain)은 서버에만 존재한다.
- 생활: capcha_service ms 브랜치 (커리큘럼 일차 순환은 생활 전용 — curriculum.py)
- 수학: 수학캡차-15단계 재이식 / 과학: capcha_service my 브랜치
- 사회: ms social(지도기호~응급처치 10유닛) 이식 완료 — 유닛=2주차 정렬(아래 _weekly_reorder)
- 국어: capcha_service jy 브랜치 (4학년 국어 13유형)
- 영어: catchap.english 10유닛 이식 — 유닛=2주차 정렬(아래 _weekly_reorder)
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

# 폴백 은행(파이썬 파일) — 문제 원본은 이제 catchap-service/banks/*.json 이 정본이고
# 로더가 DB(questions 테이블)에 적재한다(A방식). DB에 문항이 있으면 그걸 쓰고,
# 비었거나 접근 불가면(테이블 미생성·시드 전) 아래 파일 은행으로 폴백해 무중단 유지.
_FILE_BANKS: dict[str, list[dict]] = {
    "국어": korean_bank.KOREAN_FULL,
    "생활": life_bank.LIFE_FULL,
    "수학": math_bank.MATH_FULL,
    "과학": science_bank.SCIENCE_FULL,
    "사회": social_bank.SOCIAL_FULL,
    # 영어 = 문법·그림문장·생성기(텍스트) + 듣기(오디오) + 알파벳 따라쓰기(trace)
    "영어": english_bank.ENGLISH_FULL + english_listen.ENGLISH_LISTEN + english_trace.ENGLISH_TRACE,
}


def _load_from_db(db=None) -> dict[str, list[dict]] | None:
    """DB(questions)에서 과목별 문항을 order_no 순으로 로드 — 없거나 실패면 None(→ 파일 폴백).

    order_by(order_no)로 은행 리스트 순서를 복원한다(챕터 슬라이싱이 이 순서에 의존).
    앱 시작 1회 로드(파일 은행과 동일 성능). 갱신은 refresh_from_db()(아래) — 강의 유래
    문항 배치가 재기동 없이 반영되게 한다.
    db 인자: 호출자의 세션을 쓸 수 있게(테스트는 SQLite 오버라이드 세션을 주입 —
    SessionLocal 직결이면 테스트가 개발 MySQL을 건드린다). None이면 SessionLocal."""
    try:
        from app.models import Question

        if db is not None:
            rows = db.query(Question).order_by(Question.subject, Question.order_no).all()
        else:
            from app.db.session import SessionLocal

            _db = SessionLocal()
            try:
                rows = _db.query(Question).order_by(Question.subject, Question.order_no).all()
            finally:
                _db.close()
        if not rows:
            return None
        banks: dict[str, list[dict]] = {}
        for r in rows:
            banks.setdefault(r.subject, []).append(r.payload)
        return banks
    except Exception:
        return None


BANKS: dict[str, list[dict]] = _load_from_db() or _FILE_BANKS


# ── 주간 챕터 정렬: 원본 유닛 = 2주차 (사용자 결정 0714) ─────────────────────
# 영어·사회 원본 저장소는 1유닛 = 25문항(5단계 × 5문제)인데, 전체학습은 1주차 = 10문항이라
# 유닛당 '단계별 앞 4문항'(4×5=20)을 본편으로 유닛 순서대로 놓으면 1~200번이 정확히
# 20주차(유닛 N = 2N-1·2N주차, 주차 제목도 유닛 topic으로 자동 파생)가 된다. 단계별
# 5번째 문항(유닛당 5개)은 뒤로 보내 챕터에는 안 나오되 오늘의퀴즈·은행 풀에는 남는다
# (콘텐츠 유실 없음). DB(order_no)든 파일 폴백이든 로드 직후 같은 규칙을 적용하므로
# 재시드·마이그레이션 없이 항상 성립한다.
_UNIT_PREFIX: dict[str, dict[str, int]] = {
    # 영어 유닛 02=듣기(eng-listen), 03=알파벳 따라쓰기(eng-trace) — 별도 모듈 이식이라 프리픽스가 다르다
    "영어": {"eng-01": 1, "eng-listen": 2, "eng-trace": 3, "eng-04": 4, "eng-05": 5,
             "eng-06": 6, "eng-07": 7, "eng-08": 8, "eng-09": 9, "eng-10": 10},
    "사회": {f"soc-{n:02d}": n for n in range(1, 11)},
}
_CORE_PER_STAGE = 4  # 단계별 본편 문항 수 — 5단계 × 4 = 유닛당 20문항(2주차)


def _weekly_reorder(subject: str, bank: list[dict]) -> list[dict]:
    """유닛=2주차 정렬 — 본편(유닛·단계 순) 뒤에 잔여 문항. 유닛 미매칭 문항은 맨 뒤(방어)."""
    units = _UNIT_PREFIX.get(subject)
    if not units:
        return bank

    def unit_of(q: dict) -> int | None:
        qid = str(q.get("id") or "")
        for pre, n in units.items():
            if qid.startswith(pre + "-"):
                return n
        return None

    core: list[tuple[int, int, dict]] = []
    extra: list[tuple[int, int, dict]] = []
    other: list[dict] = []
    seen: dict[tuple[int, int], int] = {}
    for q in bank:
        u = unit_of(q)
        if u is None:
            other.append(q)
            continue
        stage = int(q.get("stage") or 0)
        k = (u, stage)
        c = seen.get(k, 0)
        seen[k] = c + 1
        (core if c < _CORE_PER_STAGE else extra).append((u, stage, q))
    core.sort(key=lambda t: (t[0], t[1]))  # stable — 같은 유닛·단계 안에선 원 순서 유지
    extra.sort(key=lambda t: (t[0], t[1]))
    return [q for _, _, q in core] + [q for _, _, q in extra] + other


for _s in tuple(_UNIT_PREFIX):
    if _s in BANKS:
        BANKS[_s] = _weekly_reorder(_s, BANKS[_s])

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


def refresh_from_db(db=None) -> bool:
    """DB 은행을 다시 읽어 런타임 레지스트리(BANKS/_BY_ID/_PLAYABLE)를 제자리 갱신.

    왜 필요한가: 이 레지스트리들은 임포트 시 1회 구축이라, 강의 유래 문항을 questions
    테이블에 넣어도 재기동 전까지 학생 화면에 안 보인다 — 그러면 '은행으로 보내기'
    버튼이 성공한 척만 하는 셈이다(가짜 성공 금지 원칙 위반). 배치 직후 이 함수를
    불러 즉시 반영한다.

    왜 '제자리(clear+update)'인가: 다른 모듈들이 임포트 시점에 이 dict '객체'의 참조를
    이미 들고 있다 — 새 dict로 재할당하면 그들은 옛 객체를 계속 본다. 같은 객체를
    비우고 다시 채워야 모든 참조자에게 전파된다.

    반환 False = DB가 비었거나 실패(파일 폴백 상태) — 이때 갱신하면 파일 은행 전체가
    사라지므로 아무것도 바꾸지 않는다. 호출자는 False를 정직하게 알릴 것."""
    fresh = _load_from_db(db)
    if not fresh:
        return False
    for s in tuple(_UNIT_PREFIX):
        if s in fresh:
            fresh[s] = _weekly_reorder(s, fresh[s])
    BANKS.clear()
    BANKS.update(fresh)
    _BY_ID.clear()
    _BY_ID.update({subject: {q["id"]: q for q in bank} for subject, bank in BANKS.items()})
    _PLAYABLE.clear()
    _PLAYABLE.update({subject: [q for q in bank if q["playable"]] for subject, bank in BANKS.items()})
    return True


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
