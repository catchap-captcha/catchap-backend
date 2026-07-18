"""전체학습 문제은행 — SRS(간격 반복) 상태 기계 기반 '오늘의 큐' 출제.

설계: docs/question-bank-scale-design.md (2026-07-19). 핵심 원리 — **창고는 커도
학생의 오늘 큐는 작다.** 은행이 만 개로 커져도 학생이 마주하는 건 만기 복습 + 틀린
것 + 새 문항뿐이다. (공부 키워드: spaced repetition, mastery state machine)

출제 우선순위(구 '안 푼>틀린>맞춘' 무한 순환을 대체):
  ① 만기 복습(due) — 맞힌 문항 중 다시 볼 때가 된 것(next_review_at 도래)
  ② 틀린 것(wrong) — 마지막 응답이 오답(만기 없이 즉시 재출제 후보)
  ③ 새 문항(new) — 상태 행이 없는 것
  셋 다 없으면 '오늘 완료'(None) — 무한 재순환 폐지. 단, 명시적 챕터 선택(pick_from)은
  의도적 복습이므로 휴면(resting) 폴백으로 계속 풀 수 있고, 자유 모드도 '복습 미리
  하기'(early)로 이어갈 수 있다.

상태 기계(student_question_states — 행 없음=안 푼):
  learning ──연속 2회 정답──> mastered, 오답이면 강등. 사다리 1·3·7·14·30일.
  '연속 2회'는 오답노트의 '2회 정답 = 복습완료 승격'과 같은 리듬 — 제품 전체가
  "CatChap에서 '안다'는 두 번 맞힌 것"으로 통일된다.

코인·오늘의퀴즈·연속도전은 습관 축(오늘의퀴즈) 전용 — 이 모드는 기록·정답률·오답노트만
남긴다(0713 결정 유지). 강의 완주 잠금(payload.lecture_id)은 상태 분류보다 먼저 적용.
"""

import random
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import LectureWatchProgress, StudentProfile, StudentQuestionState
from app.services import subject_banks

# ---------------------------------------------------------------- SRS 상수 (설계 §9 기본값)
# 마스터 기준: 연속 2회 정답. 사다리: streak 1→+1일, 2→+3일, 3→+7일, 4→+14일, 5+→+30일.
MASTER_STREAK = 2
SRS_LADDER_DAYS = [1, 3, 7, 14, 30]


def _now() -> datetime:
    """현재 시각 — 테스트가 시간을 조작(monkeypatch)할 수 있게 함수로 분리."""
    return datetime.now()


def _ladder_days(streak: int) -> int:
    return SRS_LADDER_DAYS[min(max(streak, 1), len(SRS_LADDER_DAYS)) - 1]


# ---------------------------------------------------------------- 강의 완주 잠금(3단계)
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


# ---------------------------------------------------------------- SRS 상태 갱신(채점 시)
def record_answer(
    db: Session, student_id: str, subject: str, question_id: str, correct: bool
) -> None:
    """서버 채점된 은행 문항 응답 1건을 SRS 상태에 반영 — _apply_attempt(단일 채점 싱크)가
    호출한다. LearningAttempt(원장)와 별개로 서빙용 상태를 파생 유지하는 것.

    get-or-create 후 갱신. 같은 (학생, 문항) 동시 첫-응답 경합은 UNIQUE 제약이 잡는데,
    학생 혼자 자기 문제를 푸는 경로라 실질 발생 확률이 없어 재시도 없이 단순하게 둔다."""
    if not question_id:
        return
    qid = str(question_id)[:80]
    row = (
        db.query(StudentQuestionState)
        .filter(
            StudentQuestionState.student_id == student_id,
            StudentQuestionState.question_id == qid,
        )
        .first()
    )
    if row is None:
        row = StudentQuestionState(student_id=student_id, question_id=qid, subject=subject)
        db.add(row)
    now = _now()
    if correct:
        row.correct_streak = int(row.correct_streak or 0) + 1
        row.last_result = "correct"
        row.state = "mastered" if row.correct_streak >= MASTER_STREAK else "learning"
        row.next_review_at = now + timedelta(days=_ladder_days(row.correct_streak))
    else:
        row.correct_streak = 0
        row.wrong_count = int(row.wrong_count or 0) + 1
        row.last_result = "incorrect"
        row.state = "learning"
        row.next_review_at = None  # 틀린 것은 만기 없이 즉시 재출제 후보
    row.last_attempt_at = now


# ---------------------------------------------------------------- 큐 분류·출제
def _states_map(db: Session, student_id: str, subject: str) -> dict[str, StudentQuestionState]:
    """그 학생·과목의 상태 행 전부 — 학생 이력 크기에 비례(풀 크기 아님). 인덱스 1회 조회."""
    rows = (
        db.query(StudentQuestionState)
        .filter(
            StudentQuestionState.student_id == student_id,
            StudentQuestionState.subject == subject,
        )
        .all()
    )
    return {r.question_id: r for r in rows}


def _classify(db: Session, student: StudentProfile, subject: str, ids: list[str]):
    """(잠금 통과한) id 후보를 (만기, 틀린, 새, 휴면)으로 분류 + 상태 맵 반환.

    휴면(resting) = 맞힌 지 얼마 안 돼 만기가 아직 안 온 문항 — 기본 출제에서 빠진다.
    구현이 풀 크기 O(n) 순회지만 DB 조회는 상태 1회라 만 개 규모에서도 가볍다."""
    by_id = _states_map(db, student.id, subject)
    now = _now()
    due: list[str] = []
    wrong: list[str] = []
    new: list[str] = []
    resting: list[str] = []
    for qid in ids:
        r = by_id.get(qid)
        if r is None:
            new.append(qid)
        elif r.last_result == "incorrect":
            wrong.append(qid)
        elif r.next_review_at is not None and r.next_review_at <= now:
            due.append(qid)
        else:
            resting.append(qid)
    return due, wrong, new, resting, by_id


def split_pool(db: Session, student: StudentProfile | None, subject: str):
    """은행 전체를 (안 푼, 틀린, 맞춘)으로 분할 — 진도 화면(progress)용 하위호환 형태.
    잠긴 강의 문항은 제외해 진도 수치가 '접근 가능한 문항' 기준이 되게 한다."""
    ids = _unlocked_ids(db, student, subject, [q["id"] for q in subject_banks.playable_pool(subject)])
    if student is None:
        return ids, [], []
    due, wrong, new, resting, _ = _classify(db, student, subject, ids)
    return new, wrong, due + resting  # 맞춘 = 만기 도래 + 휴면(마지막 응답이 정답인 것 전부)


def pick_question(
    db: Session, student: StudentProfile | None, subject: str, *, early: bool = False
) -> dict | None:
    """자유 은행 모드 출제 — 만기 → 틀린 → 새. 셋 다 없으면 None('오늘 완료').

    early=True('복습 미리 하기')면 큐 소진 시 휴면 문항 중 만기가 가장 가까운 것을
    낸다(상태 갱신은 동일 — 미리 맞히면 사다리가 한 칸 올라 만기가 더 멀어질 뿐).
    비로그인(외부 임베드)은 상태가 없으므로 잠금 적용된 풀에서 랜덤(현행 유지)."""
    ids = _unlocked_ids(db, student, subject, [q["id"] for q in subject_banks.playable_pool(subject)])
    if not ids:
        return None
    if student is None:
        return subject_banks.get_question(subject, random.choice(ids))
    due, wrong, new, resting, by_id = _classify(db, student, subject, ids)
    for group in (due, wrong, new):
        if group:
            return subject_banks.get_question(subject, random.choice(group))
    if early and resting:
        nearest = min(resting, key=lambda i: by_id[i].next_review_at or _now())
        return subject_banks.get_question(subject, nearest)
    return None  # 오늘 완료 — 호출자가 queue_status로 다음 복습일을 안내한다


def pick_from(
    db: Session, student: StudentProfile | None, subject: str, candidate_ids: list[str]
) -> dict | None:
    """후보 집합(주차 챕터 풀 등) 안에서 출제 — 만기 → 틀린 → 새 → **휴면 폴백**.

    자유 모드와 달리 '오늘 완료'로 막지 않는 이유: 챕터를 콕 집어 들어온 것은 그 자체가
    의도적 복습이다(완료한 주차 다시 풀기). 휴면 문항을 내도 상태 갱신은 동일하므로
    학습상 손해가 없다. None은 후보가 전부 잠겼거나 빈 경우뿐."""
    ids = _unlocked_ids(db, student, subject, candidate_ids)
    if not ids:
        return None
    if student is None:
        return subject_banks.get_question(subject, random.choice(ids))
    due, wrong, new, resting, _ = _classify(db, student, subject, ids)
    for group in (due, wrong, new, resting):
        if group:
            return subject_banks.get_question(subject, random.choice(group))
    return None


def queue_status(db: Session, student: StudentProfile, subject: str) -> dict:
    """오늘의 큐 현황 — '오늘 완료' 안내(다음 복습일)와 진도 카드의 원천."""
    ids = _unlocked_ids(db, student, subject, [q["id"] for q in subject_banks.playable_pool(subject)])
    due, wrong, new, resting, by_id = _classify(db, student, subject, ids)
    next_at = min(
        (by_id[i].next_review_at for i in resting if by_id[i].next_review_at), default=None
    )
    return {
        "due": len(due),
        "wrong": len(wrong),
        "new": len(new),
        "resting": len(resting),
        "next_review_at": next_at.isoformat() if next_at else None,
    }


def progress(db: Session, student: StudentProfile, subject: str) -> dict:
    """은행 진도 요약 — 문제은행 화면 카드용. due(오늘 복습 몫)를 함께 노출한다."""
    st = queue_status(db, student, subject)
    total = st["due"] + st["wrong"] + st["new"] + st["resting"]
    return {
        "subject": subject,
        "total": total,
        "unsolved": st["new"],
        "wrong": st["wrong"],
        "correct": st["due"] + st["resting"],
        # 오늘의 큐 — 화면이 "오늘 N문제"로 보여줄 수 있는 수치(만기+틀린+새는 즉시 출제 대상)
        "due": st["due"],
        "next_review_at": st["next_review_at"],
    }
