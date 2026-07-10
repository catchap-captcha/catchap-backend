"""학생 개인화 데이터가 실제 DB 테이블에서 나오는지 검증.

- PATCH /students/me/profile → nickname 실제 UPDATE + dashboard 반영
- badges → badges/student_badges 실테이블 반영
- daily-quiz → daily_quiz_status 실테이블 반영 (없으면 오늘 행 생성)
- class-ranking → 같은 반 학생 실데이터 반영
"""

from datetime import date, datetime


def _student_token(client, seed_org):
    res = client.post(
        "/api/v1/auth/student-login",
        json={
            "organization_id": seed_org["org"].id,
            "student_login_id": "stu01",
            "password": "1234",
        },
    )
    return res.json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_patch_profile_updates_nickname_and_dashboard(client, db, seed_org):
    token = _student_token(client, seed_org)

    res = client.patch(
        "/api/v1/students/me/profile",
        json={"nickname": "새별명", "age": 8},
        headers=auth(token),
    )
    assert res.status_code == 200
    assert res.json()["nickname"] == "새별명"

    # DB 행 자체가 바뀌었는지
    db.refresh(seed_org["student"])
    assert seed_org["student"].nickname == "새별명"
    assert seed_org["student"].age == 8

    # 대시보드(홈)에도 즉시 반영
    dash = client.get("/api/v1/students/me/dashboard", headers=auth(token))
    assert dash.status_code == 200
    assert dash.json()["nickname"] == "새별명"

    # 지갑(마이페이지)에도 반영
    wallet = client.get("/api/v1/students/me/wallet", headers=auth(token))
    assert wallet.json()["nickname"] == "새별명"


def test_badges_reflect_student_badges_table(client, db, seed_org):
    from app.models import Badge, StudentBadge

    b1 = Badge(
        name="첫 걸음", description="첫 학습", icon="i", color="#000",
        condition_text="첫 학습", order_no=0,
    )
    b2 = Badge(
        name="계산 왕", description="30문제", icon="i", color="#000",
        condition_text="30문제", order_no=1,
    )
    db.add_all([b1, b2])
    db.flush()
    sb = StudentBadge(
        student_id=seed_org["student"].id,
        badge_id=b1.id,
        earned_at=datetime(2026, 6, 12),
        progress=1.0,
    )
    db.add(sb)
    db.commit()

    token = _student_token(client, seed_org)
    res = client.get("/api/v1/students/me/badges", headers=auth(token))
    assert res.status_code == 200
    body = res.json()
    assert body["earned"] == 1
    assert body["locked"] == 1
    by_name = {b["name"]: b for b in body["badges"]}
    assert by_name["첫 걸음"]["earned"] is True
    assert by_name["첫 걸음"]["foot"] == "6월 12일 획득"  # earned_at 실데이터 기준
    assert by_name["계산 왕"]["earned"] is False

    # student_badges 행을 지우면 earned 감소
    db.delete(sb)
    db.commit()
    res2 = client.get("/api/v1/students/me/badges", headers=auth(token))
    assert res2.json()["earned"] == 0
    assert res2.json()["locked"] == 2

    # 대시보드 배지 카운트도 실테이블 기준
    dash = client.get("/api/v1/students/me/dashboard", headers=auth(token))
    assert dash.json()["badges"] == {"earned": 0, "total": 2}


def test_daily_quiz_reflects_daily_quiz_status(client, db, seed_org):
    from app.models import DailyQuizStatus

    token = _student_token(client, seed_org)

    # 오늘 행이 없으면 생성된다 (모두 todo)
    res = client.get("/api/v1/students/me/daily-quiz", headers=auth(token))
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == len(body["quizzes"]) > 0
    assert body["done"] == 0
    rows = (
        db.query(DailyQuizStatus)
        .filter(
            DailyQuizStatus.student_id == seed_org["student"].id,
            DailyQuizStatus.quiz_date == date.today(),
        )
        .all()
    )
    assert len(rows) == body["total"]

    # DB에서 상태를 바꾸면 응답에 반영된다
    rows[0].status = "done"
    db.commit()
    res2 = client.get("/api/v1/students/me/daily-quiz", headers=auth(token))
    assert res2.json()["done"] == 1
    done_subjects = [q["subject"] for q in res2.json()["quizzes"] if q["status"] == "done"]
    assert done_subjects == [rows[0].subject]

    # 대시보드 today도 daily_quiz_status 기준
    dash = client.get("/api/v1/students/me/dashboard", headers=auth(token))
    assert dash.json()["today"] == {"done": 1, "total": len(rows)}


def test_grade_ranking_daily_completion(client, db, seed_org):
    """랭킹: 학년별 풀 + 일일 완료 점수(정답률·속도 + 6과목 완주 보너스 30 + 연속) + 상위3 보너스 코인."""
    from app.core.security import hash_password
    from app.models import ClassRoom, DailyQuizStatus, StudentProfile

    # 같은 학년 다른 반 친구 (grade=1인 1-9반) — 학년 풀에 포함돼야 함
    other_cls = ClassRoom(organization_id=seed_org["org"].id, name="1-9반", grade=1, status="active")
    db.add(other_cls)
    db.flush()
    mate = StudentProfile(
        organization_id=seed_org["org"].id,
        class_id=other_cls.id,
        student_login_id="stu02",
        student_code="CAT-2222",
        password_hash=hash_password("1234"),
        nickname="친구닉",
        coins=999,  # 코인은 더 많지만 — 랭킹은 이제 코인이 아니라 일일 완료 점수
    )
    db.add(mate)
    db.flush()
    # 내(테스트학생)가 이틀 완료: 어제 2과목, 오늘 전과목(6과목)
    me_id = seed_org["student"].id
    yesterday = date.today() - __import__("datetime").timedelta(days=1)
    for subj in ["국어", "수학"]:
        db.add(DailyQuizStatus(student_id=me_id, quiz_date=yesterday, subject=subj, status="done"))
    for subj in ["국어", "영어", "수학", "과학", "사회", "생활"]:
        db.add(DailyQuizStatus(student_id=me_id, quiz_date=date.today(), subject=subj, status="done"))
    db.commit()

    token = _student_token(client, seed_org)
    res = client.get("/api/v1/students/me/class-ranking", headers=auth(token))
    assert res.status_code == 200
    body = res.json()
    assert body["class_size"] == 2  # 다른 반이어도 같은 학년이면 풀에 포함
    assert body["grade"] == 1
    names = [r["name"] for r in body["board"]]
    assert "친구닉" in names  # 닉네임만 노출
    me_row = next(r for r in body["board"] if r["me"])
    # 시도(learning_attempts) 기록이 없어 정답률·속도 0점. 오늘 6과목 완주 → 완주 보너스 30.
    # 어제는 2과목뿐(완주 아님), 연속 완주도 아님 → 총 30점.
    assert me_row["score"] == 30
    assert me_row["rank"] == 1  # 코인 999인 친구보다 위 (완료 기반 점수)
    # 1위 보너스 코인 30 지급 (하루 1회)
    assert body["bonus_coins"] == 30
    res2 = client.get("/api/v1/students/me/class-ranking", headers=auth(token))
    assert res2.json()["bonus_coins"] == 0  # 같은 날 중복 지급 없음


def test_replay_attempt_no_status_no_coins(client, db, seed_org):
    """복습(replay=True): 학습 기록은 남지만 오늘의퀴즈 완료 처리·코인 지급이 없다."""
    from app.models import DailyQuizStatus, StudentProfile

    token = _student_token(client, seed_org)
    before_coins = db.get(StudentProfile, seed_org["student"].id).coins

    r = client.post(
        "/api/v1/learning/attempts",
        json={"subject": "국어", "result": "correct", "score": 100, "completed": False, "replay": True},
        headers=auth(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["coins_earned"] == 0  # 복습 보상 없음

    db.expire_all()
    assert db.get(StudentProfile, seed_org["student"].id).coins == before_coins
    quiz = (
        db.query(DailyQuizStatus)
        .filter(
            DailyQuizStatus.student_id == seed_org["student"].id,
            DailyQuizStatus.quiz_date == date.today(),
            DailyQuizStatus.subject == "국어",
        )
        .first()
    )
    assert quiz is None or quiz.status != "done"  # 복습으로 오늘 완료 처리되지 않음

    # 일반 완료는 여전히 동작 (코인 + done)
    r2 = client.post(
        "/api/v1/learning/attempts",
        json={"subject": "국어", "result": "correct", "score": 100, "completed": True},
        headers=auth(token),
    )
    assert r2.status_code == 200
    assert r2.json()["coins_earned"] > 0
    db.expire_all()
    quiz2 = (
        db.query(DailyQuizStatus)
        .filter(
            DailyQuizStatus.student_id == seed_org["student"].id,
            DailyQuizStatus.quiz_date == date.today(),
            DailyQuizStatus.subject == "국어",
        )
        .first()
    )
    assert quiz2 is not None and quiz2.status == "done"


def test_game_session_server_graded(client, db, seed_org):
    """생활 실문항: 정답 미노출 발급 + 서버 채점 + 학습기록(서버판정) 저장."""
    from app.models import LearningAttempt

    token = _student_token(client, seed_org)
    res = client.get("/api/v1/students/me/game-session?subject=생활&count=3", headers=auth(token))
    assert res.status_code == 200
    body = res.json()
    assert body["available"] is True and len(body["questions"]) == 3
    for pub in body["questions"]:
        assert "answer" not in pub and "answer_id" not in pub  # 정답 미노출

    # game-answer(단일선택) 채점 경로 검증 — 뱅크에서 single 문항을 골라 제출
    from app.services import subject_banks

    real = next(q for q in subject_banks.playable_pool("생활") if q["type"] == "single")
    qid = real["id"]
    wrong = next(o["id"] for o in real["options"] if o["id"] != real["answer"])
    r1 = client.post(
        "/api/v1/students/me/game-answer",
        json={"question_id": qid, "subject": "생활", "option_id": wrong},
        headers=auth(token),
    )
    assert r1.status_code == 200
    assert r1.json()["correct"] is False
    assert r1.json()["answer_id"] == real["answer"]  # 정답 공개는 채점 후에만

    # 정답 제출 → correct + 기록 확인
    r2 = client.post(
        "/api/v1/students/me/game-answer",
        json={"question_id": qid, "subject": "생활", "option_id": real["answer"], "last": True},
        headers=auth(token),
    )
    assert r2.json()["correct"] is True

    rows = (
        db.query(LearningAttempt)
        .filter(LearningAttempt.student_id == seed_org["student"].id, LearningAttempt.subject == "생활")
        .all()
    )
    results = sorted(r.result for r in rows)
    assert results == ["correct", "incorrect"]  # 서버 판정 그대로 기록됨

    # 존재하지 않는 과목은 미지원 → available=False (전 6과목이 뱅크를 갖춰 실과목 데모는 없음)
    other = client.get("/api/v1/students/me/game-session?subject=코딩", headers=auth(token)).json()
    assert other["available"] is False


def test_game_session_new_subjects(client, db, seed_org):
    """수학·과학·사회·영어 실문항 (capcha_service my/sw/ms 이식): 발급 sanitize + 서버 채점 + 오답노트 과목 매핑."""
    from app.models import LearningAttempt, WrongAnswer

    token = _student_token(client, seed_org)
    for subject in ("수학", "과학", "사회", "영어"):
        res = client.get(
            f"/api/v1/students/me/game-session?subject={subject}&count=3", headers=auth(token)
        )
        assert res.status_code == 200, subject
        body = res.json()
        assert body["available"] is True and len(body["questions"]) == 3, subject
        for q in body["questions"]:
            # 정답·해설 미노출 + playable은 bool(원본 값은 정답 id라 유출 금지)
            assert "answer" not in q and "explain" not in q, subject
            assert q["playable"] is True, subject

    # 서버 채점: 수학 single 오답 → 오답노트가 과목·카테고리(num)로 기록
    from app.services.math_bank import MATH_FULL

    mq = next(q for q in MATH_FULL if q["type"] == "single")
    wrong = next(o["id"] for o in mq["options"] if o["id"] != mq["answer"])
    r = client.post(
        "/api/v1/students/me/game-answer",
        json={"question_id": mq["id"], "subject": "수학", "option_id": wrong},
        headers=auth(token),
    )
    assert r.status_code == 200 and r.json()["correct"] is False
    wa = db.query(WrongAnswer).filter(WrongAnswer.student_id == seed_org["student"].id).all()
    assert any(w.subject == "수학" and w.category == "num" for w in wa)

    # 사회 정답 제출 → correct + learning_attempts에 과목 그대로 기록 (single 문항 선택)
    from app.services.social_bank import SOCIAL_FULL

    hq = next(q for q in SOCIAL_FULL if q["type"] == "single")
    r2 = client.post(
        "/api/v1/students/me/game-answer",
        json={"question_id": hq["id"], "subject": "사회", "option_id": hq["answer"]},
        headers=auth(token),
    )
    assert r2.json()["correct"] is True
    rows = (
        db.query(LearningAttempt)
        .filter(LearningAttempt.student_id == seed_org["student"].id, LearningAttempt.subject == "사회")
        .all()
    )
    assert [x.result for x in rows] == ["correct"]

    # 영어 single 오답 → 오답노트 category=eng, 정답 제출 → learning_attempts 과목=영어
    from app.services.english_bank import ENGLISH_FULL

    eq = next(q for q in ENGLISH_FULL if q["type"] == "single")
    ewrong = next(o["id"] for o in eq["options"] if o["id"] != eq["answer"])
    re1 = client.post(
        "/api/v1/students/me/game-answer",
        json={"question_id": eq["id"], "subject": "영어", "option_id": ewrong},
        headers=auth(token),
    )
    assert re1.status_code == 200 and re1.json()["correct"] is False
    assert re1.json()["answer_text"] == next(o["text"] for o in eq["options"] if o["id"] == eq["answer"])
    wa2 = db.query(WrongAnswer).filter(WrongAnswer.student_id == seed_org["student"].id).all()
    assert any(w.subject == "영어" and w.category == "eng" for w in wa2)
    re2 = client.post(
        "/api/v1/students/me/game-answer",
        json={"question_id": eq["id"], "subject": "영어", "option_id": eq["answer"]},
        headers=auth(token),
    )
    assert re2.json()["correct"] is True
    assert db.query(LearningAttempt).filter(
        LearningAttempt.student_id == seed_org["student"].id, LearningAttempt.subject == "영어"
    ).count() >= 1


def test_game_session_korean(client, db, seed_org):
    """국어 실문항 (capcha_service jy 이식): 발급 sanitize + 서버 채점 + 오답노트 word + 의견 multi."""
    from app.models import LearningAttempt, WrongAnswer

    token = _student_token(client, seed_org)
    res = client.get("/api/v1/students/me/game-session?subject=국어&count=3", headers=auth(token))
    assert res.status_code == 200
    body = res.json()
    assert body["available"] is True and len(body["questions"]) == 3
    for q in body["questions"]:
        assert "answer" not in q and "explain" not in q
        assert q["playable"] is True

    # single 오답 → 오답노트 category=word(낱말·한글), 정답 → learning_attempts 과목=국어
    from app.services.korean_bank import KOREAN_FULL

    kq = next(q for q in KOREAN_FULL if q["type"] == "single")
    kwrong = next(o["id"] for o in kq["options"] if o["id"] != kq["answer"])
    r1 = client.post(
        "/api/v1/students/me/game-answer",
        json={"question_id": kq["id"], "subject": "국어", "option_id": kwrong},
        headers=auth(token),
    )
    assert r1.status_code == 200 and r1.json()["correct"] is False
    wa = db.query(WrongAnswer).filter(WrongAnswer.student_id == seed_org["student"].id).all()
    assert any(w.subject == "국어" and w.category == "word" for w in wa)
    r2 = client.post(
        "/api/v1/students/me/game-answer",
        json={"question_id": kq["id"], "subject": "국어", "option_id": kq["answer"]},
        headers=auth(token),
    )
    assert r2.json()["correct"] is True
    assert db.query(LearningAttempt).filter(
        LearningAttempt.student_id == seed_org["student"].id, LearningAttempt.subject == "국어"
    ).count() >= 1

    # 문장 부호(punct — 원본 자리탭 복원): 위젯 경로(verify) select_all 채점.
    # 부분 선택 → 오답, 정답 자리 전부(순서 무관) → 정답. (국어 multi는 원본 복원으로 소멸 —
    # multi 집합 채점 자체는 test_game_answer_multi_and_scoping(과학)이 커버)
    from app.services import captcha_service as cs

    pq = next(q for q in KOREAN_FULL if q["type"] == "punct" and len(q["answer"]) > 1)
    ch1 = cs._wrap_bank_question("국어", pq, {"subj": "국어"})
    assert "answer" not in ch1 and ch1["type"] == "punct" and ch1["tokens"]
    partial = cs.verify_challenge(db, ch1["challenge_token"], pq["answer"][:1])
    assert partial["success"] is False
    ch2 = cs._wrap_bank_question("국어", pq, {"subj": "국어"})
    ok_full = cs.verify_challenge(db, ch2["challenge_token"], list(reversed(pq["answer"])))
    assert ok_full["success"] is True


def test_game_answer_multi_and_scoping(client, db, seed_org):
    """복수선택 집합 채점(부분 정답 없음) + 과목 스코프(타 과목 id 교차 제출 404) + 비플레이 문항 400."""
    token = _student_token(client, seed_org)
    from app.services.science_bank import SCIENCE_FULL

    mq = next(q for q in SCIENCE_FULL if q["type"] == "multi")
    # 부분 제출 → 오답
    partial = client.post(
        "/api/v1/students/me/game-answer",
        json={"question_id": mq["id"], "subject": "과학", "option_ids": mq["answer"][:1]},
        headers=auth(token),
    )
    assert partial.status_code == 200 and partial.json()["correct"] is False
    # 정답 집합(순서 무관) → 정답, answer_ids로 전체 공개
    exact = client.post(
        "/api/v1/students/me/game-answer",
        json={"question_id": mq["id"], "subject": "과학", "option_ids": list(reversed(mq["answer"]))},
        headers=auth(token),
    )
    assert exact.json()["correct"] is True
    assert sorted(exact.json()["answer_ids"]) == sorted(mq["answer"])

    # 타 과목 문항 id 교차 제출 → 404 (뱅크 스코프)
    from app.services.life_bank import LIFE_FULL

    life_q = next(q for q in LIFE_FULL if q["playable"])
    spoof = client.post(
        "/api/v1/students/me/game-answer",
        json={"question_id": life_q["id"], "subject": "수학", "option_id": "o1"},
        headers=auth(token),
    )
    assert spoof.status_code == 404

    # 조작형(connect 등) 문항을 game-answer로 제출 → 400 (위젯 채점 전용)
    from app.services.social_bank import SOCIAL_FULL

    op_q = next(q for q in SOCIAL_FULL if q["type"] == "connect")
    blocked = client.post(
        "/api/v1/students/me/game-answer",
        json={"question_id": op_q["id"], "subject": "사회", "option_id": "o1"},
        headers=auth(token),
    )
    assert blocked.status_code == 400


def test_curriculum_lock_and_replay(client, db, seed_org):
    """일일 교육과정: 오늘 과제 플레이 · 지난날 복습 가능 · 다음날 잠금(주제만)."""
    token = _student_token(client, seed_org)
    res = client.get("/api/v1/students/me/curriculum?subject=생활&back=5&forward=3", headers=auth(token))
    assert res.status_code == 200
    body = res.json()
    assert body["available"] is True
    today = body["today_day"]

    days = {d["status"]: d for d in body["days"]}
    assert "today" in days and "past" in days and "future" in days
    # 오늘 주제는 날짜 순환에 따라 달라짐 — 하드코딩 대신 커리큘럼 모듈로 계산
    from app.services import curriculum as _cur

    assert days["today"]["topic"] == _cur.topic_for_index(_cur.today_index())
    assert days["today"]["playable_count"] > 0
    # 미래는 잠금 표시
    assert days["future"]["locked"] is True

    # 오늘 일차 상세: 5단계 문항 + playable 존재, 잠금 아님
    d_today = client.get(f"/api/v1/students/me/curriculum/day?subject=생활&day={today}", headers=auth(token)).json()
    assert d_today["locked"] is False
    assert len(d_today["stages"]) == 5
    assert d_today["playable_count"] > 0
    # 정답 미노출
    for s in d_today["stages"]:
        for q in s["questions"]:
            assert "answer" not in q

    # 다음날 상세: 잠금 + 주제·단계계획만(문항 없음)
    d_future = client.get(f"/api/v1/students/me/curriculum/day?subject=생활&day={today + 1}", headers=auth(token)).json()
    assert d_future["locked"] is True
    assert "topic" in d_future and "stages" not in d_future
    assert "stage_plan" in d_future

    # game-session: 미래 일차는 available=false(잠금), 오늘은 문항 발급
    fut = client.get(f"/api/v1/students/me/game-session?subject=생활&day={today + 1}", headers=auth(token)).json()
    assert fut["available"] is False and fut.get("locked") is True
    cur = client.get(f"/api/v1/students/me/game-session?subject=생활&day={today}", headers=auth(token)).json()
    assert cur["available"] is True and len(cur["questions"]) > 0
    # 지난날은 복습(is_replay=True)
    past = client.get(f"/api/v1/students/me/game-session?subject=생활&day={today - 3}", headers=auth(token)).json()
    if past["available"]:
        assert past["is_replay"] is True

def test_grade_ranking_score_formula(client, db, seed_org):
    """랭킹 산식(0708): 정답률·풀이속도 + 6과목 완주 보너스 + 연속 완주 보너스 + 상위3 코인."""
    import datetime as _dt

    from app.core.security import hash_password
    from app.models import ClassRoom, DailyQuizStatus, LearningAttempt, StudentProfile

    org_id = seed_org["org"].id
    me_id = seed_org["student"].id
    subjects = ["국어", "영어", "수학", "과학", "사회", "생활"]

    # 같은 학년 다른 반 친구 (grade=1인 1-9반) — 학년 풀에 포함돼야 함
    other_cls = ClassRoom(organization_id=org_id, name="1-9반", grade=1, status="active")
    db.add(other_cls)
    db.flush()
    mate = StudentProfile(
        organization_id=org_id,
        class_id=other_cls.id,
        student_login_id="stu02",
        student_code="CAT-2222",
        password_hash=hash_password("1234"),
        nickname="친구닉",
        coins=999,  # 코인은 더 많지만 — 랭킹은 코인이 아니라 정답률·완주 점수
    )
    db.add(mate)
    db.flush()

    yesterday = date.today() - _dt.timedelta(days=1)
    # 어제·오늘 모두 6과목 완주 + 각 과목 1문항 정답을 2초(=속도만점)에 풀었다고 기록.
    for day in (yesterday, date.today()):
        created = _dt.datetime.combine(day, _dt.time(9, 0))
        for subj in subjects:
            db.add(DailyQuizStatus(student_id=me_id, quiz_date=day, subject=subj, status="done"))
            db.add(
                LearningAttempt(
                    organization_id=org_id,
                    student_id=me_id,
                    subject=subj,
                    result="correct",
                    solve_time_ms=2000,  # <=4000 → 속도 만점 5
                    created_at=created,
                )
            )
    db.commit()

    token = _student_token(client, seed_org)
    res = client.get("/api/v1/students/me/class-ranking", headers=auth(token))
    assert res.status_code == 200
    body = res.json()
    assert body["class_size"] == 2  # 다른 반이어도 같은 학년이면 풀에 포함
    assert body["grade"] == 1
    names = [r["name"] for r in body["board"]]
    assert "친구닉" in names  # 닉네임만 노출
    me_row = next(r for r in body["board"] if r["me"])
    # 하루당: 6과목 × (정답률10 + 속도5) + 완주보너스30 = 120. 이틀 = 240.
    # 연속 보너스: 오늘의 전날(어제)도 6과목 완주 → +10. 총 250.
    assert me_row["score"] == 250, body
    assert me_row["rank"] == 1  # 코인 999인 친구보다 위 (정답률·완주 기반 점수)
    # 1위 보너스 코인 30 지급 (하루 1회)
    assert body["bonus_coins"] == 30
    res2 = client.get("/api/v1/students/me/class-ranking", headers=auth(token))
    assert res2.json()["bonus_coins"] == 0  # 같은 날 중복 지급 없음



def test_grade_ranking_incorrect_no_speed_points(client, db, seed_org):
    """오답은 정답률·속도 점수를 못 받는다 — 빠른 찍기로 점수 위조 불가."""
    import datetime as _dt

    from app.models import DailyQuizStatus, LearningAttempt, StudentProfile

    org_id = seed_org["org"].id
    me_id = seed_org["student"].id
    created = _dt.datetime.combine(date.today(), _dt.time(9, 0))
    # 국어만 완료 처리했지만 시도는 전부 오답(1ms) — 정답률 0%, 속도 0점.
    db.add(DailyQuizStatus(student_id=me_id, quiz_date=date.today(), subject="국어", status="done"))
    for _ in range(3):
        db.add(
            LearningAttempt(
                organization_id=org_id, student_id=me_id, subject="국어",
                result="incorrect", solve_time_ms=1, created_at=created,
            )
        )
    db.commit()
    _ = db.get(StudentProfile, me_id)

    token = _student_token(client, seed_org)
    body = client.get("/api/v1/students/me/class-ranking", headers=auth(token)).json()
    me_row = next(r for r in body["board"] if r["me"])
    assert me_row["score"] == 0  # 오답만 → 정답률/속도 0, 완주(6과목)도 아님 → 0점




def test_all_subjects_sticker_awarded_once(client, db, seed_org):
    """6과목 완주 스티커: 마지막 과목 done 순간 스티커+코인 지급, 하루 1회 멱등, 복습 미지급."""
    from app.models import StudentProfile

    token = _student_token(client, seed_org)
    subjects = ["국어", "영어", "수학", "과학", "사회", "생활"]
    before = db.get(StudentProfile, seed_org["student"].id).coins

    last_res = None
    for subj in subjects:
        last_res = client.post(
            "/api/v1/learning/attempts",
            json={"subject": subj, "result": "correct", "score": 100, "completed": True},
            headers=auth(token),
        ).json()
    # 여섯 번째 과목 완료 응답에 스티커+보너스 코인이 실린다
    assert last_res["sticker_awarded"] is True
    assert last_res["sticker_coins"] > 0
    # 앞선 5개 응답에는 없었을 것 — 대신 다시 완료해도 중복 지급이 없는지 확인
    again = client.post(
        "/api/v1/learning/attempts",
        json={"subject": "국어", "result": "correct", "score": 100, "completed": True},
        headers=auth(token),
    ).json()
    assert again["sticker_awarded"] is False and again["sticker_coins"] == 0

    db.expire_all()
    after = db.get(StudentProfile, seed_org["student"].id).coins
    # 학습 보상 + 스티커 보너스가 실제 잔액에 반영 (스티커 보너스는 정확히 1회)
    assert after - before >= last_res["sticker_coins"]

    # 결과 API에 오늘의 스티커 표시
    res = client.get("/api/v1/students/me/result?subject=국어", headers=auth(token)).json()
    assert res["sticker_today"] is True


def test_chapter_history_before_cut(client, db, seed_org):
    """챕터 지난 기록: chapter_no 스코프 집계 + before(이번 세션 시작) 이전만."""
    token = _student_token(client, seed_org)
    # 3챕터 기록: 정답 1 + 오답 1 → 50%
    for result in ("correct", "incorrect"):
        client.post(
            "/api/v1/learning/attempts",
            json={"subject": "수학", "result": result, "score": 0, "chapter_no": 3},
            headers=auth(token),
        )
    r = client.get(
        "/api/v1/students/me/chapter-history?subject=수학&chapter=3", headers=auth(token)
    ).json()
    assert r["total"] == 2 and r["accuracy"] == 50
    # before=과거 시각 → 그 이전 기록 없음 → accuracy null
    r2 = client.get(
        "/api/v1/students/me/chapter-history?subject=수학&chapter=3&before=2000-01-01T00:00:00",
        headers=auth(token),
    ).json()
    assert r2["total"] == 0 and r2["accuracy"] is None
    # 다른 챕터는 집계에 안 섞임
    r3 = client.get(
        "/api/v1/students/me/chapter-history?subject=수학&chapter=4", headers=auth(token)
    ).json()
    assert r3["total"] == 0


def test_chapter_replay_server_side_no_coin_farming(client, db, seed_org):
    """완주한 챕터 단계를 클라가 replay 플래그 없이 재플레이해도 서버가 복습으로 판정 → 코인 미적립."""
    from app.api.v1.endpoints.captcha_api import _credit_student
    from app.models import ChapterProgress, StudentProfile

    student = seed_org["student"]
    # 수학 1챕터를 3단계까지 완주한 상태로 세팅
    db.add(ChapterProgress(student_id=student.id, subject="수학", chapter_no=1, stages_done=3))
    db.commit()
    before = db.get(StudentProfile, student.id).coins

    # 이미 완주한 2단계를 replay 플래그 없이(rp 없음) 정답 제출 → 서버가 복습 판정
    s1 = _credit_student(db, student, {"subj": "수학", "chapter": 1, "stage": 2}, True, "o1")
    db.commit()
    assert s1["replay"] is True and s1["coins_earned"] == 0
    db.expire_all()
    assert db.get(StudentProfile, student.id).coins == before  # 코인 재적립 없음

    # 미완주 4단계는 정상 적립 경로(복습 아님)
    s2 = _credit_student(db, student, {"subj": "수학", "chapter": 1, "stage": 4}, True, "o1")
    db.commit()
    assert s2["replay"] is False and s2["coins_earned"] > 0
