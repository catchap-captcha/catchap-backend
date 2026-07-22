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
# 전체학습 주차 수 — 임시 확정(2026-07-14): 전 과목 20주차 고정. 문항이 20*10=200 미만인
# 과목(수학 153→15주치, 과학 91→9주치)은 _cycle_slice가 문항을 순환 반복해 20주차를 채운다.
MAX_CHAPTERS = 20
# 전체학습 첫 주(2026-07-06 월요일) 기준 날짜 — 주간 달력 잠금은 폐지(2026-07-22)됐고,
# 이제 students 챕터 API가 참고용 `anchor_monday`로만 내보낸다(게이팅에는 미사용).
ANCHOR_MONDAY = date(2026, 7, 6)


def max_chapters(subject: str) -> int:
    """전체학습 주차 수 — 임시 확정(2026-07-14): 전 과목 MAX_CHAPTERS(20주차) 고정.

    문항이 20주×10=200개 미만인 과목(예: 과학 91→9주치)은 부족분을 문항 순환 반복으로
    채워 동일하게 20주차를 연다(_cycle_slice). playable 문항이 하나도 없으면 0.
    """
    pool = subject_banks.playable_pool(subject)
    return MAX_CHAPTERS if pool else 0


def _cycle_slice(subject: str, start: int, count: int) -> list[dict]:
    """전역 슬롯 [start, start+count)를 문제은행에서 순환(부족하면 앞에서부터 반복)해 채운다.

    20주차 고정을 위해 풀이 20*10=200 미만인 과목은 문항을 반복하고, 초과 과목은 앞 200만
    쓴다(주차 구조가 과목마다 같도록). 슬롯이 총 200을 넘으면 빈 리스트 부분은 잘린다.
    """
    pool = subject_banks.playable_pool(subject)
    if not pool:
        return []
    total = MAX_CHAPTERS * CHAPTER_SIZE
    return [pool[i % len(pool)] for i in range(start, min(start + count, total))]


def unlocked_count(subject: str, today: date | None = None) -> int:
    """열린 챕터 수 — 성인 자유학습 전환(2026-07-22)으로 주간 달력 잠금을 폐지했다.
    이제 그 과목의 전 챕터를 항상 연다(playable 문항이 없으면 0).

    과거엔 앵커(ANCHOR_MONDAY) 이후 지난 주차로 게이팅하고 그 위에 임시 env 스위치
    (CATCHAP_UNLOCK_ALL_CHAPTERS)로 잠금을 껐다. 개방이 기본 동작이 되면서 둘 다 제거했다.
    today 인자는 호출부 호환을 위해 남기지만 더는 쓰지 않는다."""
    return max_chapters(subject)


def chapter_title(subject: str, chapter_no: int) -> str:
    """챕터 제목 — 그 챕터(10문항)를 채우는 실제 문제은행 topic으로 만든다.

    옛 Chapter 테이블의 고정 5개 이름(콘텐츠와 불일치·6주차↑ 무명)을 대체한다.
    한 챕터가 여러 topic을 걸치면 상위 2개를 '·'로 잇는다.
    """
    sliced = _cycle_slice(subject, (chapter_no - 1) * CHAPTER_SIZE, CHAPTER_SIZE)
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
    start = (chapter_no - 1) * CHAPTER_SIZE + (stage - 1) * STAGE_SIZE
    sliced = _cycle_slice(subject, start, STAGE_SIZE)
    return [subject_banks.public_question(q) for q in sliced]


def chapter_all_question_ids(subject: str, chapter_no: int) -> list[str]:
    """챕터 풀 전체(현재 10문항, 은행이 늘면 확장) 문항 id — 주차 안 우선순위 출제용.

    0713 하이브리드: 단계(5단계×2문항)는 페이스 조절이고, 어떤 문항을 낼지는
    이 풀에서 학생별 우선순위(안 푼>틀린>맞춘)로 고른다(bank_mode.pick_from)."""
    if chapter_no < 1:
        return []
    return [q["id"] for q in _cycle_slice(subject, (chapter_no - 1) * CHAPTER_SIZE, CHAPTER_SIZE)]


def chapter_question_ids(subject: str, chapter_no: int, stage: int) -> list[str]:
    """(챕터, 단계) 문항 id 목록 — 서버 검증용(제출 문항이 이 단계 소속인지 확인)."""
    if chapter_no < 1 or stage < 1 or stage > STAGES:
        return []
    pool = subject_banks.playable_pool(subject)
    start = (chapter_no - 1) * CHAPTER_SIZE + (stage - 1) * STAGE_SIZE
    return [q["id"] for q in pool[start : start + STAGE_SIZE]]
