"""전체학습 문제은행 모드 — 출제 우선순위(안 푼>틀린>맞춘)·무보상·오늘의퀴즈 미반영."""

from app.services import bank_mode, subject_banks


def _mk_attempt(db, student, subject, qid, result):
    from app.models import LearningAttempt

    db.add(
        LearningAttempt(
            organization_id=student.organization_id, student_id=student.id,
            subject=subject, chapter_no=1, content_id=qid, result=result,
            score=20 if result == "correct" else 0,
        )
    )
    db.commit()


def test_bank_priority_unsolved_then_wrong_then_correct(db, seed_org):
    student = seed_org["student"]
    subject = "수학"
    ids = [q["id"] for q in subject_banks.playable_pool(subject)]
    assert len(ids) >= 3

    # 초기: 전부 안 푼 상태
    unsolved, wrong, correct = bank_mode.split_pool(db, student, subject)
    assert len(unsolved) == len(ids) and not wrong and not correct

    # 한 문항을 틀리고, 다른 문항을 맞히면 → 분류 반영
    _mk_attempt(db, student, subject, ids[0], "incorrect")
    _mk_attempt(db, student, subject, ids[1], "correct")
    unsolved, wrong, correct = bank_mode.split_pool(db, student, subject)
    assert ids[0] in wrong and ids[1] in correct
    assert ids[0] not in unsolved and ids[1] not in unsolved

    # 안 푼 문항이 남아 있으면 출제는 반드시 안 푼 것에서
    for _ in range(10):
        q = bank_mode.pick_question(db, student, subject)
        assert q["id"] in unsolved

    # 전부 풀되 ids[0]만 오답이면 → 틀린 문제 우선
    for qid in ids[2:]:
        _mk_attempt(db, student, subject, qid, "correct")
    for _ in range(5):
        q = bank_mode.pick_question(db, student, subject)
        assert q["id"] == ids[0], "틀린 문제가 우선 출제돼야 한다"

    # 틀린 것도 다시 맞히면 → 맞춘 문제 순환(전체에서)
    _mk_attempt(db, student, subject, ids[0], "correct")
    unsolved, wrong, correct = bank_mode.split_pool(db, student, subject)
    assert not unsolved and not wrong and len(correct) == len(ids)
    assert bank_mode.pick_question(db, student, subject)["id"] in correct


def test_bank_attempt_no_coin_no_quiz(client, db, seed_org):
    """은행 모드 적립: 기록·정답률·오답노트는 남고 코인·오늘의퀴즈는 불변."""
    from app.models import CoinTransaction, DailyQuizStatus
    from app.services import captcha_service as cs
    from tests.test_wallet_shop import _first_party_key, _student_token

    _first_party_key(db)
    tok = _student_token(client)
    student = seed_org["student"]

    q = subject_banks.playable_pool("국어")
    dictation = next(x for x in q if x["type"] == "dictation")
    full = subject_banks.get_question("국어", dictation["id"])
    ch = cs._wrap_bank_question("국어", full, {"subj": "국어", "bank": True})

    r = client.post(
        "/api/v1/captcha/v1/verify",
        json={"challenge_token": ch["challenge_token"], "answer": full["answer"]},
        headers={"X-Site-Key": "ck_edu_testfp", "Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200 and r.json()["success"] is True
    sess = r.json()["session"]
    assert sess["coins_earned"] == 0, "은행 모드는 무보상"
    assert sess["quiz_bonus"] == 0

    db.refresh(student)
    assert student.coins == 100  # seed 그대로
    assert db.query(CoinTransaction).filter(CoinTransaction.student_id == student.id).count() == 0
    # 오늘의퀴즈 상태도 불변(생성/승격 없음)
    assert (
        db.query(DailyQuizStatus)
        .filter(DailyQuizStatus.student_id == student.id, DailyQuizStatus.subject == "국어")
        .count()
        == 0
    )
    # 기록은 남는다 — content_id 포함(은행 분류 원천)
    from app.models import LearningAttempt

    att = db.query(LearningAttempt).filter(LearningAttempt.student_id == student.id).first()
    assert att is not None and att.content_id == full["id"]


def test_bank_challenge_http_and_progress(client, db, seed_org):
    """?bank=true 챌린지 발급 + 진도 API가 출제 분류와 일치."""
    from tests.test_wallet_shop import _first_party_key, _student_token

    _first_party_key(db)
    tok = _student_token(client)

    r = client.post(
        "/api/v1/captcha/v1/challenge?subject=%EA%B5%AD%EC%96%B4&bank=true",
        headers={"X-Site-Key": "ck_edu_testfp", "Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    assert "challenge_token" in r.json() and "answer" not in r.json()

    p = client.get(
        "/api/v1/students/me/bank-progress", headers={"Authorization": f"Bearer {tok}"}
    )
    assert p.status_code == 200
    subjects = p.json()["subjects"]
    assert subjects, "라이브 과목 진도가 있어야 한다"
    row = next(s for s in subjects if s["subject"] == "국어")
    assert row["total"] == row["unsolved"] + row["wrong"] + row["correct"]
    assert row["total"] > 0
