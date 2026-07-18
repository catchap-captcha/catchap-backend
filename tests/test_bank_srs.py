"""문제은행 SRS — 상태 전이·사다리·오늘 완료(HTTP)·복습 미리 하기·백필 멱등.

설계: docs/question-bank-scale-design.md. 큐 우선순위 자체는 test_bank_mode의
test_bank_priority_srs_queue가 검증한다 — 여기는 상태 기계와 경계(HTTP·백필)를 본다.
"""
import urllib.parse
from datetime import datetime, timedelta

from app.models import LearningAttempt, StudentQuestionState
from app.services import bank_mode, subject_banks

from tests.test_bank_mode import _first_party_key, _student_token


def _state(db, student_id, qid) -> StudentQuestionState | None:
    return (
        db.query(StudentQuestionState)
        .filter(
            StudentQuestionState.student_id == student_id,
            StudentQuestionState.question_id == qid,
        )
        .first()
    )


def _days(a, b) -> float:
    return (a - b).total_seconds() / 86400


def test_record_answer_state_machine_and_ladder(db, seed_org):
    """정답 1회=learning(+1일) → 연속 2회=mastered(+3일) → 오답=강등(만기 소거)."""
    student = seed_org["student"]
    qid = subject_banks.playable_pool("수학")[0]["id"]

    bank_mode.record_answer(db, student.id, "수학", qid, True)
    db.commit()
    row = _state(db, student.id, qid)
    assert row.state == "learning" and row.correct_streak == 1 and row.last_result == "correct"
    assert abs(_days(row.next_review_at, row.last_attempt_at) - 1) < 0.01  # 사다리 1칸=+1일

    bank_mode.record_answer(db, student.id, "수학", qid, True)
    db.commit()
    row = _state(db, student.id, qid)
    assert row.state == "mastered" and row.correct_streak == 2  # 연속 2회=마스터(오답노트와 같은 리듬)
    assert abs(_days(row.next_review_at, row.last_attempt_at) - 3) < 0.01  # +3일

    bank_mode.record_answer(db, student.id, "수학", qid, False)
    db.commit()
    row = _state(db, student.id, qid)
    assert row.state == "learning" and row.correct_streak == 0 and row.wrong_count == 1
    assert row.last_result == "incorrect" and row.next_review_at is None  # 즉시 재출제 후보

    # 행은 (학생, 문항)당 1개 — 응답을 거듭해도 늘지 않는다(희소 유지)
    assert (
        db.query(StudentQuestionState)
        .filter(StudentQuestionState.student_id == student.id)
        .count()
        == 1
    )


def test_challenge_today_done_http_and_early_review(client, db, seed_org):
    """큐 소진 시 챌린지가 '오늘 완료'(all_done + 다음 복습일)를 주고, early=true면 휴면을 낸다."""
    _first_party_key(db)
    tok = _student_token(client)
    student = seed_org["student"]

    # 풀이 가장 작은 과목을 골라 전부 1회 정답(휴면) 상태로 만든다
    subject = min(
        sorted(subject_banks.LIVE_SUBJECTS),
        key=lambda s: len(subject_banks.playable_pool(s)),
    )
    for q in subject_banks.playable_pool(subject):
        bank_mode.record_answer(db, student.id, subject, q["id"], True)
    db.commit()

    enc = urllib.parse.quote(subject)
    headers = {"X-Site-Key": "ck_edu_testfp", "Authorization": f"Bearer {tok}"}
    r = client.post(f"/api/v1/captcha/v1/challenge?subject={enc}&bank=true", headers=headers)
    assert r.status_code == 404, r.text
    detail = r.json()["detail"]
    assert detail["all_done"] is True and detail["next_review_at"]  # 완료 + 다음 복습일 안내

    # '복습 미리 하기' — 휴면 문항을 낸다(200 + 챌린지 토큰)
    r2 = client.post(
        f"/api/v1/captcha/v1/challenge?subject={enc}&bank=true&early=true", headers=headers
    )
    assert r2.status_code == 200, r2.text
    assert "challenge_token" in r2.json() and "answer" not in r2.json()

    # 진도 API — due(오늘 복습 몫)·next_review_at이 함께 내려온다
    p = client.get("/api/v1/students/me/bank-progress", headers={"Authorization": f"Bearer {tok}"})
    assert p.status_code == 200
    row = next(s for s in p.json()["subjects"] if s["subject"] == subject)
    assert row["due"] == 0 and row["next_review_at"]
    assert row["total"] == row["unsolved"] + row["wrong"] + row["correct"]


def _raw_attempt(db, student, subject, qid, result, at):
    """SRS 훅 없이 원장(LearningAttempt)만 기록 — 백필 입력 재현용(created_at 지정)."""
    db.add(
        LearningAttempt(
            organization_id=student.organization_id, student_id=student.id,
            subject=subject, chapter_no=1, content_id=qid, result=result,
            score=20 if result == "correct" else 0, created_at=at,
        )
    )
    db.commit()


def test_backfill_rebuilds_states_idempotently(db, seed_org):
    """백필: 원장 시간순 fold(말미 연속 정답=streak)로 상태 재구축 — 재실행해도 같은 결과."""
    import manage_bank_srs

    student = seed_org["student"]
    qid = subject_banks.playable_pool("수학")[0]["id"]
    t0 = datetime(2026, 1, 1, 10, 0, 0)
    _raw_attempt(db, student, "수학", qid, "incorrect", t0)
    _raw_attempt(db, student, "수학", qid, "correct", t0 + timedelta(minutes=5))
    _raw_attempt(db, student, "수학", qid, "correct", t0 + timedelta(minutes=10))

    assert _state(db, student.id, qid) is None  # 훅을 안 탔으니 상태 없음(백필 대상)
    manage_bank_srs.backfill(db, execute=True)

    row = _state(db, student.id, qid)
    assert row.state == "mastered" and row.correct_streak == 2 and row.wrong_count == 1
    assert row.last_result == "correct"
    assert abs(_days(row.next_review_at, t0 + timedelta(minutes=10)) - 3) < 0.01  # streak 2=+3일

    # 멱등 — 재실행해도 행 수·값 동일(신규 0)
    manage_bank_srs.backfill(db, execute=True)
    rows = (
        db.query(StudentQuestionState)
        .filter(StudentQuestionState.student_id == student.id, StudentQuestionState.question_id == qid)
        .all()
    )
    assert len(rows) == 1 and rows[0].correct_streak == 2
