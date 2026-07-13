"""전체학습 문제은행 모드 — 챕터/단계/주간 잠금/코인 없이 은행 전체에서 우선순위 출제.

출제 우선순위(0713 제품 결정): ① 안 푼 문제 → ② 틀린 문제(마지막 시도가 오답) → ③ 맞춘 문제.
진도 원천은 LearningAttempt.content_id(문항 id) — 챕터 시절 기록엔 content_id가 없어
자연스럽게 '안 푼 문제'로 분류된다(부트스트랩에 유리, 마이그레이션 불필요).
코인·오늘의퀴즈·연속도전은 습관 축(오늘의퀴즈) 전용 — 이 모드는 기록·정답률·오답노트만 남긴다.
"""

import random

from sqlalchemy.orm import Session

from app.models import LearningAttempt, StudentProfile
from app.services import subject_banks


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


def split_pool(db: Session, student: StudentProfile | None, subject: str):
    """은행을 (안 푼, 틀린, 맞춘) 세 그룹으로 분할 — 출제·진도 화면 공용."""
    ids = [q["id"] for q in subject_banks.playable_pool(subject)]
    if student is None or not ids:
        return ids, [], []
    last = _last_results(db, student.id, subject, ids)
    unsolved = [i for i in ids if i not in last]
    wrong = [i for i in ids if last.get(i) == "incorrect"]
    correct = [i for i in ids if last.get(i) == "correct"]
    return unsolved, wrong, correct


def pick_question(db: Session, student: StudentProfile | None, subject: str) -> dict | None:
    """우선순위 출제: 안 푼 → 틀린 → 맞춘. 비로그인(외부)은 전체 랜덤."""
    unsolved, wrong, correct = split_pool(db, student, subject)
    for group in (unsolved, wrong, correct):
        if group:
            return subject_banks.get_question(subject, random.choice(group))
    return None


def pick_from(
    db: Session, student: StudentProfile | None, subject: str, candidate_ids: list[str]
) -> dict | None:
    """후보 집합(주차 커리큘럼의 10문항 등) 안에서 우선순위 출제 — 안 푼 → 틀린 → 맞춘.

    주차 구조(월요일 잠금)는 유지하면서 그 주 문항 선별만 은행 로직을 쓰는
    하이브리드(0713 결정)용. 비로그인은 후보 전체 랜덤."""
    if not candidate_ids:
        return None
    if student is None:
        return subject_banks.get_question(subject, random.choice(candidate_ids))
    cand = set(candidate_ids)
    unsolved, wrong, correct = split_pool(db, student, subject)
    for group in (unsolved, wrong, correct):
        scoped = [i for i in group if i in cand]
        if scoped:
            return subject_banks.get_question(subject, random.choice(scoped))
    return subject_banks.get_question(subject, random.choice(candidate_ids))


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
