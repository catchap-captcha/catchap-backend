# -*- coding: utf-8 -*-
"""문제은행 SRS 상태 백필 — LearningAttempt(원장)에서 student_question_states 재구축.

설계: docs/question-bank-scale-design.md. 상태 테이블은 파생 캐시라 언제든 이 스크립트로
다시 만들 수 있다(멱등 — 재실행하면 같은 결과로 덮어쓴다). SRS 도입 시점 1회 실행이
기본 용도이고, 상태가 의심될 때 재구축 도구로도 쓴다.

사용:
  python manage_bank_srs.py backfill            # 드라이런 — 만들 상태 행 수만 보고
  python manage_bank_srs.py backfill --execute  # 실제 upsert

규칙(런타임 record_answer와 동일):
  - 서버 채점(graded=True)·content_id 있는 시도만 반영.
  - (학생, 문항)별 시간순으로 접어(fold) 마지막 상태를 계산: 말미 연속 정답 수(streak),
    오답 누계, 마지막 결과. streak>=2면 mastered.
  - next_review_at = 마지막 시도 시각 + 사다리(1·3·7·14·30일). 마지막이 오답이면 NULL.
"""
import sys
from datetime import timedelta


def build_states(db):
    """LearningAttempt를 (학생, 문항)별로 접어 상태 dict 목록을 만든다 — 순수 계산."""
    from app.models import LearningAttempt
    from app.services.bank_mode import MASTER_STREAK, _ladder_days

    rows = (
        db.query(
            LearningAttempt.student_id,
            LearningAttempt.content_id,
            LearningAttempt.subject,
            LearningAttempt.result,
            LearningAttempt.created_at,
        )
        .filter(LearningAttempt.graded.is_(True), LearningAttempt.content_id.isnot(None))
        .order_by(LearningAttempt.student_id, LearningAttempt.content_id, LearningAttempt.created_at)
        .all()
    )
    states: dict[tuple[str, str], dict] = {}
    for student_id, content_id, subject, result, created_at in rows:
        key = (student_id, str(content_id)[:80])
        st = states.get(key)
        if st is None:
            st = {
                "student_id": key[0], "question_id": key[1], "subject": subject,
                "correct_streak": 0, "wrong_count": 0, "last_result": "", "last_attempt_at": None,
            }
            states[key] = st
        if result == "correct":
            st["correct_streak"] += 1
            st["last_result"] = "correct"
        else:
            st["correct_streak"] = 0
            st["wrong_count"] += 1
            st["last_result"] = "incorrect"
        st["subject"] = subject
        st["last_attempt_at"] = created_at
    out = []
    for st in states.values():
        if st["last_result"] == "correct":
            st["state"] = "mastered" if st["correct_streak"] >= MASTER_STREAK else "learning"
            st["next_review_at"] = (
                st["last_attempt_at"] + timedelta(days=_ladder_days(st["correct_streak"]))
                if st["last_attempt_at"] else None
            )
        else:
            st["state"] = "learning"
            st["next_review_at"] = None
        out.append(st)
    return out


def backfill(db, execute: bool) -> int:
    from app.models import StudentQuestionState

    states = build_states(db)
    existing = {
        (r.student_id, r.question_id): r for r in db.query(StudentQuestionState).all()
    }
    created = updated = 0
    for st in states:
        key = (st["student_id"], st["question_id"])
        row = existing.get(key)
        if row is None:
            created += 1
            if execute:
                db.add(StudentQuestionState(**st))
        else:
            updated += 1
            if execute:
                for k, v in st.items():
                    setattr(row, k, v)
    print(f"대상 (학생,문항) 조합: {len(states)}개 — 신규 {created} · 갱신 {updated}")
    if execute:
        db.commit()
        total = db.query(StudentQuestionState).count()
        print(f"실행 완료 — student_question_states 총 {total}행")
    else:
        print("드라이런 — 반영하려면 --execute")
    return 0


def main() -> int:
    from app.db.session import SessionLocal

    args = sys.argv[1:]
    if not args or args[0] != "backfill":
        print(__doc__)
        return 2
    db = SessionLocal()
    try:
        return backfill(db, execute="--execute" in args)
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
