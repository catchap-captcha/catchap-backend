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
    q = body["questions"][0]
    assert "answer" not in q and "answer_id" not in q  # 정답 미노출

    # 일부러 오답 제출 → 서버가 incorrect 판정
    from app.services.life_bank import get_question

    real = get_question(q["id"])
    wrong = next(o["id"] for o in real["options"] if o["id"] != real["answer"])
    r1 = client.post(
        "/api/v1/students/me/game-answer",
        json={"question_id": q["id"], "option_id": wrong},
        headers=auth(token),
    )
    assert r1.status_code == 200
    assert r1.json()["correct"] is False
    assert r1.json()["answer_id"] == real["answer"]  # 정답 공개는 채점 후에만

    # 정답 제출 → correct + 기록 확인
    r2 = client.post(
        "/api/v1/students/me/game-answer",
        json={"question_id": q["id"], "option_id": real["answer"], "last": True},
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

    # 사회 정답 제출 → correct + learning_attempts에 과목 그대로 기록
    from app.services.social_bank import SOCIAL_FULL

    hq = SOCIAL_FULL[0]
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

    # 사실·의견(multi): 부분 선택 → 오답, 의견 전체(순서 무관) → 정답
    mq = next(q for q in KOREAN_FULL if q["type"] == "multi" and len(q["answer"]) > 1)
    partial = client.post(
        "/api/v1/students/me/game-answer",
        json={"question_id": mq["id"], "subject": "국어", "option_ids": mq["answer"][:1]},
        headers=auth(token),
    )
    ok_full = client.post(
        "/api/v1/students/me/game-answer",
        json={"question_id": mq["id"], "subject": "국어", "option_ids": list(reversed(mq["answer"]))},
        headers=auth(token),
    )
    assert (partial.json()["correct"], ok_full.json()["correct"]) == (False, True)


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

    # 위젯 전용(playable=False) 문항 제출 → 400
    from app.services.math_bank import MATH_FULL

    np_q = next(q for q in MATH_FULL if not q["playable"])
    blocked = client.post(
        "/api/v1/students/me/game-answer",
        json={"question_id": np_q["id"], "subject": "수학", "option_id": "o1"},
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
