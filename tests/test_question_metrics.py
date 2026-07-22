"""문항 지표 엔드포인트(GET /ops/question-metrics) — 노출수·정답률 집계·플래그·필터·권한."""
from datetime import datetime

from app.models import LearningAttempt
from tests.test_captcha_api import _instructor, _ops, auth


def _attempts(db, student, qid, *, correct, wrong, graded=True, subject="수학"):
    """qid에 correct개 정답 + wrong개 오답 LearningAttempt를 심는다(graded 기본 True)."""
    for i in range(correct + wrong):
        db.add(LearningAttempt(
            organization_id=student.organization_id, student_id=student.id,
            subject=subject, chapter_no=0, content_id=qid,
            result="correct" if i < correct else "incorrect",
            score=20 if i < correct else 0, graded=graded, created_at=datetime.utcnow(),
        ))
    db.commit()


def test_question_metrics_aggregation_flags_and_filters(client, db, seed_org):
    student = seed_org["student"]
    # q-easy: 10/10 정답 = 100% → too_easy
    _attempts(db, student, "q-easy", correct=10, wrong=0)
    # q-hard: 2/10 = 20% → too_hard
    _attempts(db, student, "q-hard", correct=2, wrong=8)
    # q-mid: 3/6 = 50% → 플래그 없음
    _attempts(db, student, "q-mid", correct=3, wrong=3)
    # q-low: 3시도(표본 5 미만) → low_sample
    _attempts(db, student, "q-low", correct=1, wrong=2)
    # 비검증(graded=False)은 집계에서 제외돼야 한다 — q-hard에 5개 더 넣어도 안 세짐
    _attempts(db, student, "q-hard", correct=5, wrong=0, graded=False)

    otok = _ops(client, db)
    r = client.get("/api/v1/ops/question-metrics", headers=auth(otok))
    assert r.status_code == 200
    data = r.json()

    by_id = {it["id"]: it for it in data["items"]}
    assert set(by_id) == {"q-easy", "q-hard", "q-mid", "q-low"}

    # graded만 집계 — q-hard는 10(비검증 5는 제외)
    assert by_id["q-hard"]["attempts"] == 10
    assert by_id["q-hard"]["correct"] == 2
    assert by_id["q-hard"]["accuracy"] == 20
    assert "too_hard" in by_id["q-hard"]["flags"]

    assert by_id["q-easy"]["accuracy"] == 100
    assert "too_easy" in by_id["q-easy"]["flags"]

    assert by_id["q-mid"]["accuracy"] == 50
    assert by_id["q-mid"]["flags"] == []

    assert "low_sample" in by_id["q-low"]["flags"]

    s = data["summary"]
    assert s["questions"] == 4
    assert s["too_easy"] == 1 and s["too_hard"] == 1 and s["low_sample"] == 1
    # 시도 가중 평균 = 전체 정답(10+2+3+1)/전체 시도(10+10+6+3) = 16/29 ≈ 55
    assert s["attempts"] == 29
    assert s["avg_accuracy"] == round(16 / 29 * 100)


def test_question_metrics_min_attempts_and_sort(client, db, seed_org):
    student = seed_org["student"]
    _attempts(db, student, "q-easy", correct=10, wrong=0)
    _attempts(db, student, "q-hard", correct=2, wrong=8)
    _attempts(db, student, "q-low", correct=1, wrong=2)  # 3시도

    otok = _ops(client, db)

    # min_attempts=5 → q-low(3) 제외
    r = client.get("/api/v1/ops/question-metrics",
                   params={"min_attempts": 5}, headers=auth(otok))
    ids = {it["id"] for it in r.json()["items"]}
    assert ids == {"q-easy", "q-hard"}

    # sort=hardest → 정답률 오름차순, 첫 항목이 가장 어려운 q-hard
    r2 = client.get("/api/v1/ops/question-metrics",
                    params={"sort": "hardest"}, headers=auth(otok))
    assert r2.json()["items"][0]["id"] == "q-hard"


def test_question_metrics_subject_filter(client, db, seed_org):
    student = seed_org["student"]
    _attempts(db, student, "m-1", correct=5, wrong=0, subject="수학")
    _attempts(db, student, "s-1", correct=5, wrong=0, subject="과학")

    otok = _ops(client, db)
    r = client.get("/api/v1/ops/question-metrics",
                   params={"subject": "과학"}, headers=auth(otok))
    ids = {it["id"] for it in r.json()["items"]}
    assert ids == {"s-1"}


def test_question_metrics_requires_ops(client, db):
    itok = _instructor(client, db)
    r = client.get("/api/v1/ops/question-metrics", headers=auth(itok))
    assert r.status_code == 403
