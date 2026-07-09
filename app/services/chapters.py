"""전체학습 주간 챕터 — 문제은행을 10문제(5단계×2)씩 챕터로 잘라 주 단위로 여는 커리큘럼.

오늘의 퀴즈(매일 습관·연속도전)와 분리된 '학습(숙련도)' 축이다. 문항은 과목 뱅크의
playable 풀을 리스트 순서대로 슬라이싱하므로 (챕터, 단계)는 항상 같은 문항을 가리킨다
(이어하기·복습이 안정적).

은행이 커지면(수천 문항) 이 고정 슬라이싱 대신 랜덤 풀로 진화할 예정이다. 진도는
ChapterProgress(단계 커서) + LearningAttempt(푼 문항 집합) + StudentProgress.accuracy(숙련도)로
저장하므로, 랜덤풀 전환 시에도 마이그레이션 없이 진도를 그릴 수 있다.
"""

from datetime import date

from app.services import subject_banks

CHAPTER_SIZE = 10  # 한 챕터 = 10문제
STAGE_SIZE = 2  # 한 단계 = 2문제
STAGES = 5  # 챕터당 5단계
# 챕터 수는 문제은행 크기(÷10)로만 정한다 — 인위적 상한 없음(은행 늘면 자동 확장).
# 폭주 방지용 안전 상한(1년치)만 둔다.
MAX_CHAPTERS = 52
# 챕터1 = 이번 주(2026-07-06 월요일) — 전체 공통 달력 기준(모든 학생 같은 주에 같은 챕터).
ANCHOR_MONDAY = date(2026, 7, 6)


def max_chapters(subject: str) -> int:
    """그 과목 playable 문항으로 반복 없이 채울 수 있는 챕터 수(문제은행÷10).

    예(현재): 영어64→6, 생활55·수학51→5, 사회45→4, 과학33→3. 은행이 늘면 자동 확장.
    """
    pool = subject_banks.playable_pool(subject)
    return min(MAX_CHAPTERS, len(pool) // CHAPTER_SIZE)


def unlocked_count(subject: str, today: date | None = None) -> int:
    """오늘 기준 열린 챕터 수 = min(max_chapters, 앵커 이후 지난 주 + 1). 최소 1(음수 방지)."""
    today = today or date.today()
    weeks = max(0, (today - ANCHOR_MONDAY).days // 7)
    mx = max_chapters(subject)
    if mx <= 0:
        return 0
    return max(1, min(mx, weeks + 1))


def chapter_title(subject: str, chapter_no: int) -> str:
    """챕터 제목 — 그 챕터(10문항)를 채우는 실제 문제은행 topic으로 만든다.

    옛 Chapter 테이블의 고정 5개 이름(콘텐츠와 불일치·6주차↑ 무명)을 대체한다.
    한 챕터가 여러 topic을 걸치면 상위 2개를 '·'로 잇는다.
    """
    pool = subject_banks.playable_pool(subject)
    start = (chapter_no - 1) * CHAPTER_SIZE
    sliced = pool[start : start + CHAPTER_SIZE]
    if not sliced:
        return f"{chapter_no}주차"
    from collections import Counter

    topics = [q.get("topic") for q in sliced if q.get("topic")]
    if not topics:
        return f"{chapter_no}주차"
    common = [t for t, _ in Counter(topics).most_common(2)]
    return " · ".join(common)


def stage_questions(subject: str, chapter_no: int, stage: int) -> list[dict]:
    """(챕터, 단계)에 해당하는 2문항 — public_question(정답·해설 제거). 범위 밖이면 빈 리스트."""
    if chapter_no < 1 or stage < 1 or stage > STAGES:
        return []
    pool = subject_banks.playable_pool(subject)
    start = (chapter_no - 1) * CHAPTER_SIZE + (stage - 1) * STAGE_SIZE
    if start < 0 or start >= len(pool):
        return []
    sliced = pool[start : start + STAGE_SIZE]
    return [subject_banks.public_question(q) for q in sliced]


def chapter_question_ids(subject: str, chapter_no: int, stage: int) -> list[str]:
    """(챕터, 단계) 문항 id 목록 — 서버 검증용(제출 문항이 이 단계 소속인지 확인)."""
    if chapter_no < 1 or stage < 1 or stage > STAGES:
        return []
    pool = subject_banks.playable_pool(subject)
    start = (chapter_no - 1) * CHAPTER_SIZE + (stage - 1) * STAGE_SIZE
    return [q["id"] for q in pool[start : start + STAGE_SIZE]]
