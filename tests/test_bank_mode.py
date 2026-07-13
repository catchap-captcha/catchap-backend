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


def test_chapter_hybrid_priority_and_no_coin(client, db, seed_org):
    """주차 커리큘럼 하이브리드 — 챕터 풀 안에서 안 푼 우선 출제 + 무보상 + 챕터번호 기록."""
    from app.models import CoinTransaction, LearningAttempt
    from app.services import chapters as ch_mod
    from tests.test_wallet_shop import _first_party_key, _student_token

    _first_party_key(db)
    tok = _student_token(client)
    student = seed_org["student"]

    ids = ch_mod.chapter_all_question_ids("국어", 1)
    assert len(ids) == 10  # 현재 챕터 크기 — 늘어나도 로직은 그대로

    # 챕터 1의 9문항을 이미 풀었다고 기록 → 남은 1문항이 우선 출제돼야 한다
    for qid in ids[:9]:
        _mk_attempt(db, student, "국어", qid, "correct")

    seen = set()
    for _ in range(6):
        r = client.post(
            "/api/v1/captcha/v1/challenge?subject=%EA%B5%AD%EC%96%B4&chapter=1&stage=1",
            headers={"X-Site-Key": "ck_edu_testfp", "Authorization": f"Bearer {tok}"},
        )
        assert r.status_code == 200, r.text
        # 챌린지 응답엔 qid가 없다(정답 비노출) — 프롬프트로 대상 문항 판별
        from app.services import subject_banks

        target = subject_banks.get_question("국어", ids[9])
        assert r.json()["prompt"] == target["prompt"], "안 푼 문항이 우선 출제돼야 한다"
        seen.add(r.json()["prompt"])
    assert len(seen) == 1

    # 챕터 플레이 적립: 무보상 + 실제 챕터 번호 기록 + 오늘의퀴즈 미오염
    q = subject_banks.get_question("국어", ids[9])
    if q["type"] == "dictation":
        from app.services import captcha_service as cs

        wrapped = cs._wrap_bank_question(
            "국어", q, {"subj": "국어", "rp": False, "chapter": 1, "stage": 1, "bank": True}
        )
        vr = client.post(
            "/api/v1/captcha/v1/verify",
            json={"challenge_token": wrapped["challenge_token"], "answer": q["answer"]},
            headers={"X-Site-Key": "ck_edu_testfp", "Authorization": f"Bearer {tok}"},
        )
        assert vr.status_code == 200
        assert vr.json()["session"]["coins_earned"] == 0
    row = (
        db.query(LearningAttempt)
        .filter(LearningAttempt.student_id == student.id, LearningAttempt.content_id == ids[0])
        .first()
    )
    assert row is not None and row.chapter_no == 1
    assert db.query(CoinTransaction).filter(CoinTransaction.student_id == student.id).count() == 0


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
