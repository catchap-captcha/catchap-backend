"""전체학습 문제은행 모드 — 챕터/단계/주간 잠금/코인 없이 은행 전체에서 우선순위 출제.

출제 우선순위(0713 제품 결정): ① 안 푼 문제 → ② 틀린 문제(마지막 시도가 오답) → ③ 맞춘 문제.
진도 원천은 LearningAttempt.content_id(문항 id) — 챕터 시절 기록엔 content_id가 없어
자연스럽게 '안 푼 문제'로 분류된다(부트스트랩에 유리, 마이그레이션 불필요).
코인·오늘의퀴즈·연속도전은 습관 축(오늘의퀴즈) 전용 — 이 모드는 기록·정답률·오답노트만 남긴다.
"""

import random

from sqlalchemy.orm import Session

from app.models import LearningAttempt, LectureWatchProgress, StudentProfile
from app.services import subject_banks


# ---------------------------------------------------------------- 강의 완주 잠금(문제은행 3단계)
# 강의에서 문제은행으로 배치한 문항은 payload.lecture_id를 갖는다(배치 시 저장). 그 문항은
# 학생이 '그 강의를 완주(LectureWatchProgress.status=done)'해야 열린다 — 시청 검증 철학
# (실제로 봐야 그 강의의 연습 문제를 푼다). 강의 무관 문항(기존 은행 1,000+개)은 항상 열림.
def completed_lecture_ids(db: Session, student_id: str) -> set[str]:
    """이 학생이 완주한 강의 id 집합(status='done'). student_id 인덱스로 효율적."""
    return {
        r[0]
        for r in db.query(LectureWatchProgress.lecture_id)
        .filter(
            LectureWatchProgress.student_id == student_id,
            LectureWatchProgress.status == "done",
        )
        .all()
    }


def is_unlocked(q: dict | None, completed: set[str] | None) -> bool:
    """이 문항이 이 학생에게 열려 있나. 강의 유래 문항(payload.lecture_id 있음)은 그 강의를
    완주해야(completed에 포함) 열린다. 강의 무관 문항은 항상 열림. completed=None(비로그인·
    완주 정보 없음)이면 강의 유래 문항은 잠금(외부 임베드가 강의 문항을 흘리지 않게)."""
    lec = (q or {}).get("lecture_id")
    if not lec:
        return True
    return completed is not None and lec in completed


def _unlocked_ids(
    db: Session, student: StudentProfile | None, subject: str, ids: list[str]
) -> list[str]:
    """id 후보에서 강의 완주 잠금을 적용해 접근 가능한 것만 남긴다."""
    completed = completed_lecture_ids(db, student.id) if student else None
    return [i for i in ids if is_unlocked(subject_banks.get_question(subject, i), completed)]


def unlocked_pool(db: Session, student: StudentProfile | None, subject: str) -> list[dict]:
    """playable_pool에 강의 완주 잠금을 적용한 문항 dicts — 세트 발급(무작위 샘플)용."""
    completed = completed_lecture_ids(db, student.id) if student else None
    return [q for q in subject_banks.playable_pool(subject) if is_unlocked(q, completed)]


def _last_results(db: Session, student_id: str, subject: str, ids: list[str]) -> dict[str, str]:
    """문항별 마지막 시도 결과 — {qid: 'correct'|'incorrect'} (시도 없으면 키 없음)."""
    rows = (
        db.query(LearningAttempt.content_id, LearningAttempt.result, LearningAttempt.created_at)
        .filter(
            LearningAttempt.student_id == student_id,
            LearningAttempt.subject == subject,
            LearningAttempt.content_id.in_(ids),
        )
        .order_by(LearningAttempt.created_at.asc())
        .all()
    )
    last: dict[str, str] = {}
    for qid, result, _ in rows:  # 시간 오름차순이라 마지막 대입이 최신 결과
        if qid:
            last[qid] = result
    return last


def _split_ids(db: Session, student: StudentProfile | None, subject: str, ids: list[str]):
    """주어진 문항 id들을 (안 푼, 틀린, 맞춘)으로 분할 — 이력 조회는 이 id 집합만 대상."""
    if student is None or not ids:
        return ids, [], []
    last = _last_results(db, student.id, subject, ids)
    unsolved = [i for i in ids if i not in last]
    wrong = [i for i in ids if last.get(i) == "incorrect"]
    correct = [i for i in ids if last.get(i) == "correct"]
    return unsolved, wrong, correct


def split_pool(db: Session, student: StudentProfile | None, subject: str):
    """은행 전체를 (안 푼, 틀린, 맞춘)으로 분할 — 진도 화면(progress)용.
    잠긴 강의 문항은 제외해 진도 수치가 '접근 가능한 문항' 기준이 되게 한다."""
    ids = _unlocked_ids(db, student, subject, [q["id"] for q in subject_banks.playable_pool(subject)])
    return _split_ids(db, student, subject, ids)


def _pick(db: Session, student: StudentProfile | None, subject: str, ids: list[str]) -> dict | None:
    """id 후보 안에서 우선순위 출제: 안 푼 → 틀린 → 맞춘. 비로그인은 전체 랜덤.
    강의 완주 잠금을 먼저 적용 — 미완주 강의 유래 문항은 후보에서 빠진다(다 잠기면 None)."""
    ids = _unlocked_ids(db, student, subject, ids)
    if not ids:
        return None
    if student is None:
        return subject_banks.get_question(subject, random.choice(ids))
    for group in _split_ids(db, student, subject, ids):
        if group:
            return subject_banks.get_question(subject, random.choice(group))
    return subject_banks.get_question(subject, random.choice(ids))


def pick_question(db: Session, student: StudentProfile | None, subject: str) -> dict | None:
    """우선순위 출제(은행 전체): 안 푼 → 틀린 → 맞춘. 비로그인(외부)은 전체 랜덤."""
    return _pick(db, student, subject, [q["id"] for q in subject_banks.playable_pool(subject)])


def pick_from(
    db: Session, student: StudentProfile | None, subject: str, candidate_ids: list[str]
) -> dict | None:
    """후보 집합(주차 커리큘럼의 10문항 등) 안에서 우선순위 출제 — 안 푼 → 틀린 → 맞춘.

    주차 구조(월요일 잠금)는 유지하면서 그 주 문항 선별만 은행 로직을 쓰는 하이브리드(0713).
    이력 조회를 후보 id로만 좁혀(과목 은행 전체가 아니라) 핫패스 IN절·분류 비용을 줄인다."""
    return _pick(db, student, subject, candidate_ids)


def progress(db: Session, student: StudentProfile, subject: str) -> dict:
    """은행 진도 요약 — 전체학습 화면 카드용."""
    unsolved, wrong, correct = split_pool(db, student, subject)
    total = len(unsolved) + len(wrong) + len(correct)
    return {
        "subject": subject,
        "total": total,
        "unsolved": len(unsolved),
        "wrong": len(wrong),
        "correct": len(correct),
    }
