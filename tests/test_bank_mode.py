"""전체학습 문제은행 모드 — 출제 우선순위(안 푼>틀린>맞춘)·무보상·오늘의퀴즈 미반영."""

from app.services import bank_mode, subject_banks


# --- 공용 헬퍼 (은퇴한 test_wallet_shop에서 이식 — 은행/오답 테스트가 공유) ---
def _student_token(client):
    r = client.post(
        "/api/v1/auth/student-login",
        json={"student_login_id": "stu01", "password": "1234"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _first_party_key(db):
    from app.models import ApiKey, Organization, Site

    platform = Organization(name="CatChap플랫폼", code="TS-CAT-9100", org_type="플랫폼")
    db.add(platform)
    db.flush()
    site = Site(organization_id=platform.id, name="inapp", domain="")
    db.add(site)
    db.flush()
    key = ApiKey(
        organization_id=platform.id, site_id=site.id, product="edu", subject="국어",
        site_key="ck_edu_testfp", secret_key_hash="x", first_party=True,
    )
    db.add(key)
    db.commit()
    return key


def _mk_attempt(db, student, subject, qid, result):
    from app.models import LearningAttempt

    db.add(
        LearningAttempt(
            organization_id=student.organization_id, student_id=student.id,
            subject=subject, chapter_no=1, content_id=qid, result=result,
            score=20 if result == "correct" else 0,
        )
    )
    # 런타임 채점 싱크(_apply_attempt)와 동일하게 SRS 상태도 갱신 — 서빙 분류의 정본이
    # LearningAttempt 스캔에서 student_question_states로 바뀌었다(설계 question-bank-scale-design.md).
    bank_mode.record_answer(db, student.id, subject, qid, result == "correct")
    db.commit()


def test_bank_priority_srs_queue(db, seed_org, monkeypatch):
    """SRS 큐(만기→틀린→새): 휴면 제외·소진 시 '오늘 완료'·복습 미리 하기·만기 재등장.

    구 '안 푼>틀린>맞춘 무한 순환'을 대체하는 스펙(설계: question-bank-scale-design.md)."""
    from datetime import timedelta

    student = seed_org["student"]
    subject = "수학"
    ids = [q["id"] for q in subject_banks.playable_pool(subject)]
    assert len(ids) >= 3

    # 초기: 전부 안 푼 상태
    unsolved, wrong, correct = bank_mode.split_pool(db, student, subject)
    assert len(unsolved) == len(ids) and not wrong and not correct

    # 한 문항을 틀리고 다른 문항을 맞히면 → 분류 반영 + '틀린 것'이 새 문항보다 우선
    _mk_attempt(db, student, subject, ids[0], "incorrect")
    _mk_attempt(db, student, subject, ids[1], "correct")
    unsolved, wrong, correct = bank_mode.split_pool(db, student, subject)
    assert ids[0] in wrong and ids[1] in correct
    assert ids[0] not in unsolved and ids[1] not in unsolved
    for _ in range(5):
        assert bank_mode.pick_question(db, student, subject)["id"] == ids[0], "틀린 문항 우선"

    # 틀린 걸 다시 맞히면 휴면(만기 전) — 이제 새 문항에서만 나온다(맞춘 것 재순환 없음)
    _mk_attempt(db, student, subject, ids[0], "correct")
    fresh = set(ids[2:])
    for _ in range(10):
        assert bank_mode.pick_question(db, student, subject)["id"] in fresh

    # 전부 한 번씩 맞히면 → '오늘 완료'(None) + 다음 복습일 안내. 무한 재순환 폐지.
    for qid in ids[2:]:
        _mk_attempt(db, student, subject, qid, "correct")
    unsolved, wrong, correct = bank_mode.split_pool(db, student, subject)
    assert not unsolved and not wrong and len(correct) == len(ids)
    assert bank_mode.pick_question(db, student, subject) is None
    st = bank_mode.queue_status(db, student, subject)
    assert st["resting"] == len(ids) and st["next_review_at"]

    # '복습 미리 하기'(early)는 휴면에서 낸다 — 완료가 강제 종료는 아니다
    assert bank_mode.pick_question(db, student, subject, early=True)["id"] in correct
    # 챕터 등 명시 선택(pick_from)은 휴면 폴백 — 완료한 주차 복습이 막히지 않는다
    assert bank_mode.pick_from(db, student, subject, ids[:3])["id"] in ids[:3]

    # 만기(사다리 1일)가 지나면 복습으로 다시 나온다
    real_now = bank_mode._now
    monkeypatch.setattr(bank_mode, "_now", lambda: real_now() + timedelta(days=2))
    assert bank_mode.pick_question(db, student, subject)["id"] in correct


def test_bank_attempt_no_coin_no_quiz(client, db, seed_org):
    """은행 모드 적립: 기록·정답률·오답노트는 남고 코인·오늘의퀴즈는 불변."""
    from app.models import CoinTransaction, DailyQuizStatus
    from app.services import captcha_service as cs

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


def test_verify_long_qid_and_answerless_wrong(client, db, seed_org):
    """0713 라이브 500 회귀 — ① 36자 초과 문항 id 적립, ② answer 키 없는 유형(input) 오답."""
    from app.models import LearningAttempt
    from app.services import captcha_service as cs
    from app.services import subject_banks

    _first_party_key(db)
    tok = _student_token(client)
    student = seed_org["student"]
    headers = {"X-Site-Key": "ck_edu_testfp", "Authorization": f"Bearer {tok}"}

    # ① 최장급 슬러그 id 문항 — verify가 500 없이 적립돼야 한다
    long_q = max(
        (q for s in sorted(subject_banks.LIVE_SUBJECTS) for q in subject_banks.playable_pool(s)),
        key=lambda q: len(q["id"]),
    )
    subj = next(
        s for s in sorted(subject_banks.LIVE_SUBJECTS)
        if any(q["id"] == long_q["id"] for q in subject_banks.playable_pool(s))
    )
    assert len(long_q["id"]) > 36, "36자 초과 id가 있어야 회귀를 잡는다"
    ch = cs._wrap_bank_question(subj, long_q, {"subj": subj, "bank": True})
    r = client.post(
        "/api/v1/captcha/v1/verify",
        json={"challenge_token": ch["challenge_token"], "answer": "오답용-임의값"},
        headers=headers,
    )
    assert r.status_code == 200, r.text  # 500(Data too long)이면 실패
    att = (
        db.query(LearningAttempt)
        .filter(LearningAttempt.student_id == student.id, LearningAttempt.content_id == long_q["id"])
        .first()
    )
    assert att is not None, "긴 id도 잘리지 않고 저장돼야 한다"

    # ② input 유형(answer 키 없음, answers 목록) 오답 — KeyError 없이 처리
    input_q = next(
        (q for s in sorted(subject_banks.LIVE_SUBJECTS)
         for q in subject_banks.playable_pool(s) if q["type"] == "input"),
        None,
    )
    if input_q is not None:
        subj2 = next(
            s for s in sorted(subject_banks.LIVE_SUBJECTS)
            if any(q["id"] == input_q["id"] for q in subject_banks.playable_pool(s))
        )
        ch2 = cs._wrap_bank_question(subj2, input_q, {"subj": subj2, "bank": True})
        r2 = client.post(
            "/api/v1/captcha/v1/verify",
            json={"challenge_token": ch2["challenge_token"], "answer": "완전 틀린 답"},
            headers=headers,
        )
        assert r2.status_code == 200, r2.text  # KeyError('answer')면 500


def test_dont_know_marks_wrong_with_explain(client, db, seed_org):
    """'잘 모르겠어요'(answer=null) — 찍기 강요 없이 오답 처리 + 해설 응답 + SRS wrong 상자 기록.

    결정 ④(Q 통합): 옛 오답노트(WrongAnswer) 쓰기는 은퇴 — 오답의 정본은 StudentQuestionState."""
    from app.models import StudentQuestionState, WrongAnswer
    from app.services import captcha_service as cs
    from app.services import subject_banks

    _first_party_key(db)
    tok = _student_token(client)
    student = seed_org["student"]
    headers = {"X-Site-Key": "ck_edu_testfp", "Authorization": f"Bearer {tok}"}

    q = next(
        x for s in sorted(subject_banks.LIVE_SUBJECTS) for x in subject_banks.playable_pool(s)
        if x["type"] == "single" and (x.get("explain") or x.get("hint"))
    )
    subj = next(
        s for s in sorted(subject_banks.LIVE_SUBJECTS)
        if any(x["id"] == q["id"] for x in subject_banks.playable_pool(s))
    )
    ch = cs._wrap_bank_question(subj, q, {"subj": subj, "bank": True})
    r = client.post(
        "/api/v1/captcha/v1/verify",
        json={"challenge_token": ch["challenge_token"], "answer": None},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is False, "무응답은 오답으로 채점돼야 한다(운 좋은 정답 없음)"
    assert body.get("explain"), "공부용 해설이 내려와야 한다"
    # 옛 오답노트에는 신규 기록이 생기지 않는다(쓰기 은퇴 — 데이터 보존, 쓰기만 중단)
    assert (
        db.query(WrongAnswer)
        .filter(WrongAnswer.student_id == student.id, WrongAnswer.question == q["prompt"])
        .first()
        is None
    )
    # 대신 SRS 상태에 오답으로 남아 '틀린 문제' 뷰·최우선 재출제의 근거가 된다
    st = (
        db.query(StudentQuestionState)
        .filter(
            StudentQuestionState.student_id == student.id,
            StudentQuestionState.question_id == q["id"],
        )
        .first()
    )
    assert st is not None and st.last_result == "incorrect" and st.wrong_count >= 1


def test_chapter_clamp_on_subject_without_that_week(client, db, seed_org):
    """과목 이동 자동 보정 — 그 과목에 없는 주차를 요청하면 마지막 열린 주차로 clamp(404 아님)."""
    from app.services import chapters as _ch

    _first_party_key(db)
    tok = _student_token(client)

    # 문제은행이 가장 작은 과목(주차 수 적음)을 골라 그 최대를 초과하는 주차 요청
    subj = min(
        ("국어", "영어", "수학", "과학", "사회", "생활"),
        key=lambda s: _ch.max_chapters(s),
    )
    mx = _ch.max_chapters(subj)
    assert mx >= 1
    over = mx + 5  # 존재하지 않는 주차

    import urllib.parse

    r = client.post(
        f"/api/v1/captcha/v1/challenge?subject={urllib.parse.quote(subj)}&chapter={over}&stage=1",
        headers={"X-Site-Key": "ck_edu_testfp", "Authorization": f"Bearer {tok}"},
    )
    # clamp가 되면 200 + 정상 문항, 안 되면 404("플레이할 문항이 없어요")
    assert r.status_code == 200, f"{subj} {over}주차가 마지막 주차로 보정돼야 한다: {r.text}"
    assert "challenge_token" in r.json()


def test_bank_challenge_http_and_progress(client, db, seed_org):
    """?bank=true 챌린지 발급 + 진도 API가 출제 분류와 일치."""

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


def test_bank_options_shuffled_per_issue_and_grading_safe(client, db, seed_org):
    """전체학습 은행 보기는 발급마다 순서가 섞이고(정답 위치 암기·행동데이터 편향 방지),
    그래도 채점은 안전하다(answer가 옵션 id 기반이라 위치 무관)."""
    from app.services import captcha_service as cs
    from app.services import korean_bank

    q = next(x for x in korean_bank.KOREAN_FULL if x["type"] == "single")

    orders = {tuple(o["id"] for o in cs._wrap_bank_question("국어", q, {"subj": "국어", "qid": q["id"]})["options"])
              for _ in range(10)}
    assert len(orders) > 1, "보기 순서가 발급마다 고정 — 셔플이 작동하지 않는다"

    # 채점 안전: 셔플된 발급본에서도 정답 옵션 id가 그대로 존재하고, 그 id로 통과한다
    _first_party_key(db)
    tok = _student_token(client)
    ch = cs._wrap_bank_question("국어", q, {"subj": "국어", "qid": q["id"]})
    assert q["answer"] in [o["id"] for o in ch["options"]]  # 정답 보존
    r = client.post(
        "/api/v1/captcha/v1/verify",
        json={"challenge_token": ch["challenge_token"], "answer": q["answer"]},
        headers={"X-Site-Key": "ck_edu_testfp", "Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200 and r.json()["success"] is True
