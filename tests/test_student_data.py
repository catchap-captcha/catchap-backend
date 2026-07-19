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


def _single_q(subject):
    from app.services import subject_banks

    return next(x for x in subject_banks.playable_pool(subject) if x["type"] == "single")


def _game_answer(client, token, subject, correct, last=False, q=None):
    """game-answer(서버가 정답 검증 = graded 경로)로 single 문항 1건 제출 — graded 시도 생성.

    무채점 자기신고(/learning/attempts)는 점수 부수효과가 없으므로, done·코인·스티커를
    검증하는 테스트는 반드시 이 서버 채점 경로를 써야 한다(적대적검토 #4/#5 수정 이후)."""
    q = q or _single_q(subject)
    ans = str(q["answer"])
    opt = ans if correct else next(str(o["id"]) for o in q["options"] if str(o["id"]) != ans)
    r = client.post(
        "/api/v1/students/me/game-answer",
        json={"subject": subject, "question_id": q["id"], "option_id": opt, "last": last},
        headers=auth(token),
    )
    assert r.status_code == 200, r.text
    return r.json()


def _complete_subject(client, token, subject):
    """게임 채점으로 5문항 정답 → 오늘의퀴즈 done 승격(graded≥5 + 정답). 마지막 응답 반환."""
    q = _single_q(subject)
    res = None
    for i in range(5):
        res = _game_answer(client, token, subject, correct=True, last=(i == 4), q=q)
    return res


def test_quiz_status_unified_on_attempts(client, db, seed_org):
    """'오늘의 퀴즈 현황' 단일 기준(2026-07-13): 오늘 5문항(서버 채점=graded)을 다 풀면
    마지막이 오답이라 daily_quiz_status가 done으로 승격 안 돼도 화면상 '완료'로 본다.
    홈·오늘의퀴즈 페이지·결과화면 다음 과목이 이 기준을 공유해 역주행하지 않는다."""
    from app.models import DailyQuizStatus

    token = _student_token(client, seed_org)

    # 국어 5문항을 게임 채점(graded)으로 소진(마지막 오답) → status는 done 아님
    q = _single_q("국어")
    for i in range(5):
        _game_answer(client, token, "국어", correct=False, last=(i == 4), q=q)
    quiz = (
        db.query(DailyQuizStatus)
        .filter(
            DailyQuizStatus.student_id == seed_org["student"].id,
            DailyQuizStatus.quiz_date == date.today(),
            DailyQuizStatus.subject == "국어",
        )
        .first()
    )
    assert quiz is None or quiz.status != "done"  # 오답 소진 — 정답완주 아님(코인·랭킹 미지급)

    # 홈 대시보드: 국어가 '완료'(graded 시도 5 기준)로 잡힌다
    dash = client.get("/api/v1/students/me/dashboard", headers=auth(token)).json()
    kor = next(s for s in dash["subjects"] if s["subject"] == "국어")
    assert kor["state"] == "done" and kor["done"] == kor["total"]
    assert dash["today"]["done"] >= 1

    # 오늘의퀴즈 페이지: 국어 카드도 '완료' 배지
    dq = client.get("/api/v1/students/me/daily-quiz", headers=auth(token)).json()
    kor_card = next(c for c in dq["quizzes"] if c["subject"] == "국어")
    assert kor_card["status"] == "done" and kor_card["stage_done"] == kor_card["stages"]

    # 결과화면 다음 과목: 소진한 국어는 today_done에 포함 → 다음 과목으로 다시 나오지 않는다
    res = client.get("/api/v1/students/me/result?subject=국어", headers=auth(token)).json()
    assert "국어" in res["today_done"]
    order = res["subject_order"]
    next_undone = next((s for s in order if s not in set(res["today_done"])), None)
    assert next_undone != "국어"


def test_self_report_attempts_grant_no_score(client, db, seed_org):
    """적대적검토 #4/#5 회귀: 무채점 자기신고(/learning/attempts)로는 오늘의퀴즈 done·랭킹·
    코인·스티커·화면상 완료를 전부 얻을 수 없다. 6과목 completed:true를 보내도 무효."""
    from app.models import DailyQuizStatus, StudentProfile

    token = _student_token(client, seed_org)
    before = db.get(StudentProfile, seed_org["student"].id).coins
    for subj in ["국어", "영어", "수학", "과학", "사회", "생활"]:
        r = client.post(
            "/api/v1/learning/attempts",
            json={"subject": subj, "result": "correct", "score": 100, "completed": True, "solve_time_ms": 1},
            headers=auth(token),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["coins_earned"] == 0 and body["sticker_awarded"] is False

    db.expire_all()
    done = (
        db.query(DailyQuizStatus)
        .filter(
            DailyQuizStatus.student_id == seed_org["student"].id,
            DailyQuizStatus.status == "done",
        )
        .count()
    )
    assert done == 0  # done 승격 없음 → 랭킹·스티커 근거 안 생김
    assert db.get(StudentProfile, seed_org["student"].id).coins == before  # 코인 미증가
    # 대시보드 '완료'도 없음(_played는 graded만) — 자기신고로 표시 위조도 불가
    dash = client.get("/api/v1/students/me/dashboard", headers=auth(token)).json()
    assert dash["today"]["done"] == 0




def test_chapter_stats_two_axis(client, db, seed_org):
    """전체학습 챕터별 정답률 집계 — 은행모드(chapter_no=0) 제외, 오늘의 퀴즈(NULL) 분리 노출."""
    token = _student_token(client, seed_org)

    def _att(**kw):
        r = client.post("/api/v1/learning/attempts", json={"subject": "수학", "score": 0, **kw},
                        headers=auth(token))
        assert r.status_code == 200

    # 수학 1챕터: 5시도 중 4정답(80%) / 2챕터: 2시도 중 1정답(50%, 표본부족)
    for res in ["correct", "correct", "correct", "correct", "incorrect"]:
        _att(result=res, chapter_no=1)
    for res in ["correct", "incorrect"]:
        _att(result=res, chapter_no=2)
    # 오늘의 퀴즈(습관, chapter_no 없음): 5시도 전부 정답(100%) — 분리 노출용
    for _ in range(5):
        _att(result="correct", daily=True)
    # 은행모드(chapter_no=0): 5시도 전부 오답 — 종합 정답률에 절대 섞이면 안 됨
    for _ in range(5):
        _att(result="incorrect", chapter_no=0)

    res = client.get("/api/v1/students/me/chapter-stats", headers=auth(token)).json()
    mat = next(s for s in res["subjects"] if s["subject"] == "수학")
    by_no = {c["no"]: c for c in mat["chapters"]}
    assert by_no[1]["accuracy"] == 80 and by_no[1]["total"] == 5 and by_no[1]["low_sample"] is False
    assert by_no[2]["accuracy"] == 50 and by_no[2]["total"] == 2 and by_no[2]["low_sample"] is True
    # 종합 = 챕터(≥1)만 = 5/7 ≈ 71. 은행 0/5가 섞였다면 5/12=42 → 71이어야 제외 증명.
    assert mat["overall_accuracy"] == round(5 / 7 * 100)
    # 오늘의 퀴즈 정답률은 분리 필드로 100, 종합엔 미포함
    assert mat["daily_quiz_accuracy"] == 100
    # 미학습 챕터는 accuracy=null (0%로 표시하지 않음)
    assert any(c["accuracy"] is None and c["total"] == 0 for c in mat["chapters"])


def test_habit_stats_streak(client, db, seed_org):
    """오늘의 퀴즈 습관 축 일별 집계 — 오늘 시도가 있으면 연속일 ≥1, 그날 done/accuracy 반영."""
    token = _student_token(client, seed_org)
    for _ in range(5):
        client.post("/api/v1/learning/attempts",
                    json={"subject": "국어", "result": "correct", "score": 0, "daily": True},
                    headers=auth(token))
    res = client.get("/api/v1/students/me/habit-stats?weeks=1", headers=auth(token)).json()
    assert res["streak"] >= 1
    last = res["days"][-1]  # 오늘
    assert last["attempts"] >= 5 and last["done"] >= 1 and last["accuracy"] == 100


def test_wrong_view_srs_from_game_answer(client, db, seed_org):
    """'틀린 문제' 뷰(결정 ④) — game-answer 오답이 SRS wrong 상자로 화면에 노출되고,
    한 번 다시 맞히면 뷰에서 즉시 이탈한다. 옛 '2회 정답 복습완료 승격'은 은퇴 —
    같은 리듬(연속 2회)은 SRS 마스터 축이 담당하고, 뷰는 last_result만 본다."""
    from app.models import StudentQuestionState, WrongAnswer
    from app.services import subject_banks

    token = _student_token(client, seed_org)
    q = next(x for x in subject_banks.playable_pool("수학") if x["type"] == "single")
    correct = str(q["answer"])
    wrong = next(str(o["id"]) for o in q["options"] if str(o["id"]) != correct)

    # 오답 → 신규 WrongAnswer 없음(쓰기 은퇴), SRS 뷰에 과목·카테고리·틀린 횟수로 노출
    r = client.post("/api/v1/students/me/game-answer",
                    json={"subject": "수학", "question_id": q["id"], "option_id": wrong, "chapter_no": 2},
                    headers=auth(token))
    assert r.status_code == 200 and r.json()["correct"] is False
    assert (
        db.query(WrongAnswer)
        .filter(WrongAnswer.student_id == seed_org["student"].id)
        .count()
        == 0
    )
    notes = client.get("/api/v1/students/me/wrong-notes", headers=auth(token)).json()
    note = next(n for n in notes["items"] if n["id"] == q["id"])
    assert note["subject"] == "수학" and note["cat"] == "num" and note["wrong_count"] >= 1
    assert notes["summary"]["total"] >= 1

    # 정답 1회 → last_result가 correct로 바뀌어 뷰에서 자동 이탈("다시 맞히면 사라져요")
    r2 = client.post("/api/v1/students/me/game-answer",
                     json={"subject": "수학", "question_id": q["id"], "option_id": correct, "chapter_no": 2},
                     headers=auth(token))
    assert r2.status_code == 200 and r2.json()["correct"] is True
    notes2 = client.get("/api/v1/students/me/wrong-notes", headers=auth(token)).json()
    assert all(n["id"] != q["id"] for n in notes2["items"])

    # 마스터 축은 뷰와 별개 — 연속 1회라 아직 learning(연속 2회부터 mastered)
    st = (
        db.query(StudentQuestionState)
        .filter(
            StudentQuestionState.student_id == seed_org["student"].id,
            StudentQuestionState.question_id == q["id"],
        )
        .first()
    )
    assert st is not None and st.state == "learning" and st.correct_streak == 1




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

    # Q 통합 2단계(0719): 퀴즈 승격·코인 지급 자체가 은퇴 — 정식 서버 채점 5문항 정답이어도
    # done 승격이 없고 코인도 늘지 않는다(기록·정답률·SRS 상태만 남는다).
    _complete_subject(client, token, "국어")
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
    assert quiz2 is None or quiz2.status != "done"  # 승격 은퇴 — done이 되지 않는다
    assert db.get(StudentProfile, seed_org["student"].id).coins == before_coins  # 코인 지급 중단


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
    """수학·과학·사회·영어 실문항 (capcha_service my/sw/ms 이식): 발급 sanitize + 서버 채점
    + '틀린 문제' 뷰(SRS) 과목·카테고리 매핑(결정 ④: WrongAnswer 쓰기 은퇴)."""
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
    from app.services import subject_banks as _sb
    from app.services.math_bank import MATH_FULL

    # get_question은 봇 방지로 보기 위치를 시드 셔플하므로 서빙(=채점) 기준 정답을 써야 한다
    # (raw MATH_FULL 정답 id는 셔플 후와 다를 수 있음).
    mq = _sb.get_question("수학", next(q for q in MATH_FULL if q["type"] == "single")["id"])
    wrong = next(o["id"] for o in mq["options"] if o["id"] != mq["answer"])
    r = client.post(
        "/api/v1/students/me/game-answer",
        json={"question_id": mq["id"], "subject": "수학", "option_id": wrong},
        headers=auth(token),
    )
    assert r.status_code == 200 and r.json()["correct"] is False
    # 결정 ④: WrongAnswer 신규 기록 없음 — 과목·카테고리 매핑은 '틀린 문제' 뷰(SRS 파생)가 담당
    assert db.query(WrongAnswer).filter(WrongAnswer.student_id == seed_org["student"].id).count() == 0
    notes = client.get("/api/v1/students/me/wrong-notes", headers=auth(token)).json()
    assert any(n["subject"] == "수학" and n["cat"] == "num" for n in notes["items"])

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
    assert db.query(WrongAnswer).filter(WrongAnswer.student_id == seed_org["student"].id).count() == 0
    notes2 = client.get("/api/v1/students/me/wrong-notes", headers=auth(token)).json()
    assert any(n["subject"] == "영어" and n["cat"] == "eng" for n in notes2["items"])
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
    """국어 실문항 (capcha_service jy 이식): 발급 sanitize + 서버 채점 + '틀린 문제' 뷰 word + 의견 multi."""
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
    assert db.query(WrongAnswer).filter(WrongAnswer.student_id == seed_org["student"].id).count() == 0
    notes = client.get("/api/v1/students/me/wrong-notes", headers=auth(token)).json()
    assert any(n["subject"] == "국어" and n["cat"] == "word" for n in notes["items"])
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








def test_all_subjects_sticker_retired(client, db, seed_org):
    """Q 통합 2단계(0719): 6과목을 전부 서버 채점으로 완주해도 스티커·코인이 지급되지 않는다.

    (구 스펙: 마지막 과목 done 순간 스티커+코인 — 게임화 은퇴의 완결로 지급 루프 제거.
    기록·정답률·SRS 상태는 그대로 남는다.)"""
    from app.models import StudentProfile

    token = _student_token(client, seed_org)
    subjects = ["국어", "영어", "수학", "과학", "사회", "생활"]
    before = db.get(StudentProfile, seed_org["student"].id).coins

    last_res = None
    for subj in subjects:
        last_res = _complete_subject(client, token, subj)
    assert last_res["sticker_awarded"] is False and last_res["sticker_coins"] == 0
    assert last_res.get("quiz_bonus", 0) == 0 and last_res.get("coins_earned", 0) == 0

    db.expire_all()
    assert db.get(StudentProfile, seed_org["student"].id).coins == before  # 잔액 불변

    # 결과 API의 오늘의 스티커도 항상 False(지급 자체가 없음)
    res = client.get("/api/v1/students/me/result?subject=국어", headers=auth(token)).json()
    assert res["sticker_today"] is False


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

    # 미완주 4단계 — 복습 판정은 아님(기록 구분은 유지). 코인은 Q 통합 2단계(0719)로
    # 전면 지급 중단이라 정상 플레이도 0이다(파밍 가드가 아니라 지급 자체가 없음).
    s2 = _credit_student(db, student, {"subj": "수학", "chapter": 1, "stage": 4}, True, "o1")
    db.commit()
    assert s2["replay"] is False and s2["coins_earned"] == 0
    db.expire_all()
    assert db.get(StudentProfile, student.id).coins == before  # 잔액 불변

# (배지·학년랭킹·프로필 편집 테스트는 게임화 은퇴(0718)로 대상 엔드포인트와 함께 제거)
