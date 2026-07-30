"""코스 수료 시험(완전학습) — 강사 CRUD·응시 자격·mastery·수료·재제출/파밍 방어.

설계: docs/course-exam-design.md. 핵심 검증:
- 미완주 403(강의 다 봐야 응시), source 필수 400(기출 비영리 전제),
- mastery(틀린 것만 재출제·누적 전 문항 정답=수료), perfect(첫 시도 전부 정답),
- 재제출 409·미제출 회차 재사용(새로고침 파밍 차단), 보기 셔플 순열 서버 복원.
"""

import datetime as dt

from app.models import Course, CourseExamAttempt, CourseExamQuestion, LectureWatchProgress
from tests.test_captcha_api import _instructor, _ops, auth
from tests.test_lectures import (
    _attach_image,
    _image_id_from_url,
    _upload_lecture,
    media_dir,  # noqa: F401 (fixture — LECTURE_MEDIA_DIR을 tmp로, 리포에 파일 안 남김)
)
from tests.test_student_data import _student_token


def _mk_course(client, tok, db, *, subject="수학", title="수학 개념완성"):
    return client.post(
        "/api/v1/ops/courses", json={"title": title, "subject": subject}, headers=auth(tok)
    ).json()


def _assign_lecture(client, tok, course_id, *, subject="수학", title="1강"):
    lec = _upload_lecture(client, tok, title=title, subject=subject, duration=600).json()
    r = client.put(
        f"/api/v1/ops/lectures/{lec['id']}", json={"course_id": course_id}, headers=auth(tok)
    )
    assert r.status_code == 200, r.text
    return lec


def _add_exam_q(client, tok, course_id, *, prompt, options, answer_indexes,
                origin="manual", source=None, status="active"):
    body = {"prompt": prompt, "options": options, "answer_indexes": answer_indexes,
            "origin": origin, "status": status}
    if source is not None:
        body["source"] = source
    return client.post(
        f"/api/v1/ops/courses/{course_id}/exam-questions", json=body, headers=auth(tok)
    )


def _complete_lecture(db, student_id, lecture_id):
    db.add(LectureWatchProgress(
        student_id=student_id, lecture_id=lecture_id, watched_max_sec=600,
        next_checkpoint_sec=None, checkpoints_passed=1, status="done",
    ))
    db.commit()


def _ready(db, sitting_id, *, seconds=600):
    """회차 발급 시각을 과거로 당긴다 — 서버의 '문항당 최소 풀이 시간' 게이트 통과용.

    실서비스에선 학생이 문제를 읽는 동안 자연히 흐르는 시간이라 걸릴 일이 없지만,
    테스트는 발급 직후 제출하므로 429가 난다. 실제로 기다리는 대신 시각을 옮긴다.
    """
    from app.models import CourseExamSitting

    st = db.get(CourseExamSitting, sitting_id)
    st.created_at = st.created_at - dt.timedelta(seconds=seconds)
    db.commit()
    return sitting_id


def _cool_off(db, course_id, *, minutes=60):
    """오답 기록 시각을 과거로 당겨 쿨다운을 만료시킨다.

    서버는 방금 틀린 문항을 EXAM_WRONG_COOLDOWN_MIN 분간 재출제하지 않는다(자동화 방어).
    쿨다운 자체를 검증하는 테스트가 아니면 이걸로 넘기고 원래 보려던 것을 본다.
    """
    from app.models import CourseExamAttempt

    for a in (db.query(CourseExamAttempt)
                .filter(CourseExamAttempt.course_id == course_id,
                        CourseExamAttempt.result == "incorrect").all()):
        a.created_at = a.created_at - dt.timedelta(minutes=minutes)
    db.commit()


def _answer_all_correct(db, sess):
    """세션 문항 → 표시 순서 기준 정답 picks 목록(전부 정답)."""
    answers = []
    for item in sess["questions"]:
        q = db.get(CourseExamQuestion, item["question_id"])
        correct_texts = {q.options[i] for i in q.answer_indexes}
        picks = [i for i, opt in enumerate(item["options"]) if opt in correct_texts]
        answers.append({"question_id": q.id, "picks": picks})
    return answers


def _submit_all_correct(client, stok, course_id, db, *, perfect=False):
    """현재 회차(perfect=True면 완벽 도전)를 발급받아 전 문항 정답으로 제출 — 결과 dict 반환.

    상태를 진행시키는 용도라 오답 쿨다운은 먼저 풀어 둔다(쿨다운은 별도 테스트에서 본다).
    """
    _cool_off(db, course_id)
    url = f"/api/v1/courses/{course_id}/exam/session" + ("?perfect=true" if perfect else "")
    sess = client.post(url, headers=auth(stok)).json()
    if sess.get("passed") and not sess.get("questions"):
        return sess
    return client.post(
        f"/api/v1/courses/{course_id}/exam/submit",
        json={"sitting_id": _ready(db, sess["sitting_id"]), "answers": _answer_all_correct(db, sess)},
        headers=auth(stok),
    ).json()


# ---------------------------------------------------------------- 강사 CRUD·검증
def test_past_exam_requires_source(client, db, seed_org):
    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    # 기출인데 출처 없음 → 400 (비영리 교육용 이용 전제, 설계 §2)
    r = _add_exam_q(client, tok, course["id"], prompt="2+3=?", options=["4", "5", "6"],
                    answer_indexes=[1], origin="past_exam", source=None)
    assert r.status_code == 400 and "출처" in r.json()["detail"]
    # 출처 있으면 통과 + 화면 노출용으로 저장됨
    r2 = _add_exam_q(client, tok, course["id"], prompt="2+3=?", options=["4", "5", "6"],
                     answer_indexes=[1], origin="past_exam", source="2024 수능 수학 1번")
    assert r2.status_code == 200 and r2.json()["source"] == "2024 수능 수학 1번"


def test_exam_question_validation(client, db, seed_org):
    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    # 정답 번호가 보기 범위를 벗어남 → 400
    r = _add_exam_q(client, tok, course["id"], prompt="q", options=["a", "b"], answer_indexes=[5])
    assert r.status_code == 400
    # 보기 1개 → 400
    r2 = _add_exam_q(client, tok, course["id"], prompt="q", options=["a"], answer_indexes=[0])
    assert r2.status_code == 400


def test_exam_question_scope_other_instructor_404(client, db, seed_org):
    """강사 소유 스코프 — 남의 코스 시험 문항은 404(존재 여부 은닉, _get_ops_course 재사용)."""
    from app.core.security import hash_password
    from app.models import User

    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    # 다른 강사
    other = User(email="inst2@t.dev", password_hash=hash_password("Password123!"),
                 name="다른강사", role="instructor",
                 email_verified_at=__import__("datetime").datetime.utcnow())
    db.add(other)
    db.commit()
    otok = client.post("/api/v1/auth/ops-login",
                       json={"email": "inst2@t.dev", "password": "Password123!"}).json()["access_token"]
    r = _add_exam_q(client, otok, course["id"], prompt="q", options=["a", "b"], answer_indexes=[0])
    assert r.status_code == 404


# ---------------------------------------------------------------- 학생: 응시 자격
def test_exam_locked_until_all_lectures_done(client, db, seed_org):
    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    _assign_lecture(client, tok, course["id"], title="1강")
    lec2 = _assign_lecture(client, tok, course["id"], title="2강")
    _add_exam_q(client, tok, course["id"], prompt="q", options=["a", "b"], answer_indexes=[0])

    stok = _student_token(client, seed_org)
    # 완주 0/2 → 상태는 available False, 세션 발급 403
    st = client.get(f"/api/v1/courses/{course['id']}/exam", headers=auth(stok)).json()
    assert st["available"] is False and st["lectures_done"] == 0 and st["lectures_total"] == 2
    r = client.post(f"/api/v1/courses/{course['id']}/exam/session", headers=auth(stok))
    assert r.status_code == 403 and "완주" in r.json()["detail"]

    # 강의 전부 완주 → 열린다
    for lec in db.query(__import__("app.models", fromlist=["Lecture"]).Lecture).filter_by(course_id=course["id"]).all():
        _complete_lecture(db, seed_org["student"].id, lec.id)
    st2 = client.get(f"/api/v1/courses/{course['id']}/exam", headers=auth(stok)).json()
    assert st2["available"] is True and st2["lectures_done"] == 2
    assert lec2  # (참조 — 완주 루프가 2강 포함)


def test_no_exam_when_no_active_questions(client, db, seed_org):
    """활성 문항 0개 = '시험 없는 코스'. has_exam False, 세션 발급 404."""
    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    lec = _assign_lecture(client, tok, course["id"])
    _complete_lecture(db, seed_org["student"].id, lec["id"])
    # draft 문항만 있음(active 아님)
    _add_exam_q(client, tok, course["id"], prompt="q", options=["a", "b"],
                answer_indexes=[0], status="draft")

    stok = _student_token(client, seed_org)
    st = client.get(f"/api/v1/courses/{course['id']}/exam", headers=auth(stok)).json()
    assert st["has_exam"] is False
    r = client.post(f"/api/v1/courses/{course['id']}/exam/session", headers=auth(stok))
    assert r.status_code == 404


# ---------------------------------------------------------------- mastery·수료
def test_mastery_retry_only_wrong_until_pass(client, db, seed_org):
    """완전학습 — 회차마다 정복 못 한 것만 나오고, 누적 전 문항 정답 시 수료.
    한 문항을 일부러 틀리면 그것만 다시 나온다(만점 1회 강제 아님).

    단 방금 틀린 문항은 쿨다운 동안 나오지 않는다(자동화 방어) — 낼 게 남지 않으면
    빈 회차 대신 cooldown 응답으로 언제 열리는지 알려준다. 쿨다운이 지나면 재출제된다."""
    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    lec = _assign_lecture(client, tok, course["id"])
    _complete_lecture(db, seed_org["student"].id, lec["id"])
    for i in range(3):
        _add_exam_q(client, tok, course["id"], prompt=f"q{i}", options=["a", "b", "c"],
                    answer_indexes=[i % 3])

    stok = _student_token(client, seed_org)
    # 1회차: 3문항 중 2개만 맞히고 1개는 일부러 오답
    sess = client.post(f"/api/v1/courses/{course['id']}/exam/session", headers=auth(stok)).json()
    assert len(sess["questions"]) == 3 and sess["progress"] == {"mastered": 0, "total": 3}
    answers = []
    wrong_qid = None
    for idx, item in enumerate(sess["questions"]):
        q = db.get(CourseExamQuestion, item["question_id"])
        correct_texts = {q.options[i] for i in q.answer_indexes}
        picks = [i for i, opt in enumerate(item["options"]) if opt in correct_texts]
        if idx == 0:
            wrong_qid = q.id
            picks = [i for i, opt in enumerate(item["options"]) if opt not in correct_texts][:1]
        answers.append({"question_id": q.id, "picks": picks})
    res = client.post(f"/api/v1/courses/{course['id']}/exam/submit",
                      json={"sitting_id": _ready(db, sess["sitting_id"]), "answers": answers},
                      headers=auth(stok)).json()
    assert res["correct"] == 2 and res["passed"] is False
    assert res["progress"] == {"mastered": 2, "total": 3}

    # 방금 틀렸으므로 쿨다운 — 남은 미정복이 그 하나뿐이라 낼 게 없다.
    # 빈 회차를 주는 대신 언제 다시 열리는지 알려준다.
    cd = client.post(f"/api/v1/courses/{course['id']}/exam/session", headers=auth(stok)).json()
    assert cd["cooldown"] is True and "sitting_id" not in cd
    assert 0 < cd["retry_after_sec"] <= cd["cooldown_minutes"] * 60
    assert cd["progress"] == {"mastered": 2, "total": 3}

    # 쿨다운이 지나면 그 문항만 다시 나온다
    _cool_off(db, course["id"])
    sess2 = client.post(f"/api/v1/courses/{course['id']}/exam/session", headers=auth(stok)).json()
    assert len(sess2["questions"]) == 1
    assert sess2["questions"][0]["question_id"] == wrong_qid

    # 그 문항 정답 → 전 문항 정복 → 수료(단, 오답 이력 있으니 perfect 아님)
    q = db.get(CourseExamQuestion, wrong_qid)
    item = sess2["questions"][0]
    correct_texts = {q.options[i] for i in q.answer_indexes}
    picks = [i for i, opt in enumerate(item["options"]) if opt in correct_texts]
    res2 = client.post(f"/api/v1/courses/{course['id']}/exam/submit",
                       json={"sitting_id": _ready(db, sess2["sitting_id"]),
                             "answers": [{"question_id": wrong_qid, "picks": picks}]},
                       headers=auth(stok)).json()
    assert res2["passed"] is True and res2["perfect"] is False
    assert res2["progress"] == {"mastered": 3, "total": 3}

    # 수료 후 세션 요청 → passed 반환(재응시 아님)
    after = client.post(f"/api/v1/courses/{course['id']}/exam/session", headers=auth(stok)).json()
    assert after["passed"] is True


def test_perfect_when_all_correct_one_sitting(client, db, seed_org):
    """완벽 통과 = 현재 활성 전 문항을 한 회차에 모두 맞힘(0719 재정의). 작은 코스는
    첫 회차가 곧 전 문항이라 첫 판 무결점이 그대로 완벽 통과."""
    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    lec = _assign_lecture(client, tok, course["id"])
    _complete_lecture(db, seed_org["student"].id, lec["id"])
    for i in range(2):
        _add_exam_q(client, tok, course["id"], prompt=f"q{i}", options=["a", "b"], answer_indexes=[i % 2])

    stok = _student_token(client, seed_org)
    res = _submit_all_correct(client, stok, course["id"], db)
    assert res["passed"] is True and res["perfect"] is True


def test_perfect_challenge_upgrade_after_pass(client, db, seed_org):
    """★0719 정책 재설계 — 한 번 틀려 완벽을 놓치고 수료해도, '완벽 도전'(전 문항 한 판)을
    아싸면 완벽 통과로 승급한다(옛 규칙의 '한 번 틀리면 영구 박탈' 폐지 = 재도전 경로).
    perfect 판정은 오답 이력을 보지 않으므로 공정성 문제(삭제 문항 오답)도 함께 사라진다."""
    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    lec = _assign_lecture(client, tok, course["id"])
    _complete_lecture(db, seed_org["student"].id, lec["id"])
    for i in range(3):
        _add_exam_q(client, tok, course["id"], prompt=f"q{i}", options=["a", "b", "c"], answer_indexes=[i % 3])
    stok = _student_token(client, seed_org)

    # 1회차: 한 문항 일부러 오답(→ 첫 판 무결점 실패)
    sess = client.post(f"/api/v1/courses/{course['id']}/exam/session", headers=auth(stok)).json()
    answers = []
    for idx, item in enumerate(sess["questions"]):
        q = db.get(CourseExamQuestion, item["question_id"])
        ct = {q.options[a] for a in q.answer_indexes}
        picks = [i for i, o in enumerate(item["options"]) if o in ct]
        if idx == 0:
            picks = [i for i, o in enumerate(item["options"]) if o not in ct][:1]
        answers.append({"question_id": q.id, "picks": picks})
    r = client.post(f"/api/v1/courses/{course['id']}/exam/submit",
                    json={"sitting_id": _ready(db, sess["sitting_id"]), "answers": answers}, headers=auth(stok)).json()
    assert r["passed"] is False and r["perfect"] is False

    # 2회차: 틀린 것만 맞혀 수료(완벽 아님 — 한 회차 무결점이 아님)
    res = _submit_all_correct(client, stok, course["id"], db)
    assert res["passed"] is True and res["perfect"] is False

    # 상태: 완벽 도전 가능(수료했지만 미완벽)
    st = client.get(f"/api/v1/courses/{course['id']}/exam", headers=auth(stok)).json()
    assert st["passed"] and not st["perfect"] and st["can_perfect_challenge"] is True

    # 완벽 도전 발급 → 전 문항(3)을 한 회차에
    ch = client.post(f"/api/v1/courses/{course['id']}/exam/session?perfect=true", headers=auth(stok)).json()
    assert ch["perfect_challenge"] is True and len(ch["questions"]) == 3
    up = client.post(f"/api/v1/courses/{course['id']}/exam/submit",
                     json={"sitting_id": _ready(db, ch["sitting_id"]), "answers": _answer_all_correct(db, ch)},
                     headers=auth(stok)).json()
    assert up["passed"] is True and up["perfect"] is True

    # 승급 확정 — 이제 완벽 도전 불가(더 올릴 게 없음)
    st2 = client.get(f"/api/v1/courses/{course['id']}/exam", headers=auth(stok)).json()
    assert st2["perfect"] is True and st2["can_perfect_challenge"] is False


def test_perfect_challenge_only_after_pass(client, db, seed_org):
    """완벽 도전은 수료 후 전용 — 미수료 학생이 perfect=true를 보내도 일반 회차로 처리하고,
    can_perfect_challenge는 수료 전까지 False(두 모드가 학생 상태로 유일하게 갈린다)."""
    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    lec = _assign_lecture(client, tok, course["id"])
    _complete_lecture(db, seed_org["student"].id, lec["id"])
    for i in range(2):
        _add_exam_q(client, tok, course["id"], prompt=f"q{i}", options=["a", "b"], answer_indexes=[i % 2])
    stok = _student_token(client, seed_org)

    st = client.get(f"/api/v1/courses/{course['id']}/exam", headers=auth(stok)).json()
    assert st["can_perfect_challenge"] is False  # 아직 미수료
    # 미수료 + perfect=true → 일반 회차(완벽 도전 아님)
    sess = client.post(f"/api/v1/courses/{course['id']}/exam/session?perfect=true", headers=auth(stok)).json()
    assert sess["perfect_challenge"] is False


def test_no_answer_is_wrong(client, db, seed_org):
    """무응답('잘 모르겠어요' = 빈 picks)은 오답 — 운 좋은 정답 없음(설계 §3)."""
    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    lec = _assign_lecture(client, tok, course["id"])
    _complete_lecture(db, seed_org["student"].id, lec["id"])
    _add_exam_q(client, tok, course["id"], prompt="q", options=["a", "b"], answer_indexes=[0])

    stok = _student_token(client, seed_org)
    sess = client.post(f"/api/v1/courses/{course['id']}/exam/session", headers=auth(stok)).json()
    res = client.post(f"/api/v1/courses/{course['id']}/exam/submit",
                      json={"sitting_id": _ready(db, sess["sitting_id"]),
                            "answers": [{"question_id": sess["questions"][0]["question_id"], "picks": []}]},
                      headers=auth(stok)).json()
    assert res["correct"] == 0 and res["results"][0]["correct"] is False


# ---------------------------------------------------------------- 방어: 재제출·재사용
def test_resubmit_conflict_and_open_sitting_reuse(client, db, seed_org):
    """제출된 회차 재제출 409 + 미제출 회차는 재발급 시 그대로 재사용(파밍 차단)."""
    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    lec = _assign_lecture(client, tok, course["id"])
    _complete_lecture(db, seed_org["student"].id, lec["id"])
    for i in range(2):
        _add_exam_q(client, tok, course["id"], prompt=f"q{i}", options=["a", "b"], answer_indexes=[0])

    stok = _student_token(client, seed_org)
    s1 = client.post(f"/api/v1/courses/{course['id']}/exam/session", headers=auth(stok)).json()
    # 재요청 → 같은 sitting_id (새 조합을 굴리지 않음)
    s2 = client.post(f"/api/v1/courses/{course['id']}/exam/session", headers=auth(stok)).json()
    assert s1["sitting_id"] == s2["sitting_id"]

    # 제출
    client.post(f"/api/v1/courses/{course['id']}/exam/submit",
                json={"sitting_id": _ready(db, s1["sitting_id"]), "answers": []}, headers=auth(stok))
    # 같은 회차 재제출 → 409
    r = client.post(f"/api/v1/courses/{course['id']}/exam/submit",
                    json={"sitting_id": _ready(db, s1["sitting_id"]), "answers": []}, headers=auth(stok))
    assert r.status_code == 409


def test_stale_sitting_reissued_after_option_edit(client, db, seed_org):
    """★skeptic CONFIRMED 회귀 — 발급된 회차 중 강사가 보기 수를 바꾸면 저장된 순열이
    어긋나 채점 시 500(그 학생 시험 영구 봉쇄)이던 버그. 이제: 재발급 시 그 회차를
    폐기하고 새 순열로 다시 내고, 제출해도 500 없이 stale로 정직하게 처리한다."""
    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    lec = _assign_lecture(client, tok, course["id"])
    _complete_lecture(db, seed_org["student"].id, lec["id"])
    q = _add_exam_q(client, tok, course["id"], prompt="q", options=["a", "b", "c"],
                    answer_indexes=[0]).json()

    stok = _student_token(client, seed_org)
    s1 = client.post(f"/api/v1/courses/{course['id']}/exam/session", headers=auth(stok)).json()
    sitting_id = s1["sitting_id"]

    # 강사가 보기 수를 늘리고 정답을 새 인덱스로 이동(발급 후 편집 — order.index(3)이 터지던 경로)
    r = client.put(f"/api/v1/ops/courses/{course['id']}/exam-questions/{q['id']}",
                   json={"options": ["a", "b", "c", "d"], "answer_indexes": [3]}, headers=auth(tok))
    assert r.status_code == 200

    # 제출 경로 방어: 어긋난 회차를 그대로 제출해도 500 없이 stale로 처리(채점 제외 — 봉쇄 없음)
    old = client.post(f"/api/v1/courses/{course['id']}/exam/submit",
                      json={"sitting_id": _ready(db, sitting_id),
                            "answers": [{"question_id": q["id"], "picks": [0]}]},
                      headers=auth(stok))
    assert old.status_code == 200, old.text
    assert old.json()["stale"] == 1 and old.json()["total"] == 0

    # 발급 경로 방어: 다음 회차는 어긋난 것을 폐기하고 새 순열로 다시 낸다(4보기)
    s2 = client.post(f"/api/v1/courses/{course['id']}/exam/session", headers=auth(stok))
    assert s2.status_code == 200, s2.text
    assert s2.json()["sitting_id"] != sitting_id
    assert len(s2.json()["questions"][0]["options"]) == 4


def test_shrink_options_mid_flight_no_crash(client, db, seed_org):
    """옵션 축소 — 발급 시점 순열이 사라진 큰 인덱스를 참조해 session·submit이 IndexError로
    터지던 경로. 재발급이 크래시 없이 유효 회차를 준다."""
    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    lec = _assign_lecture(client, tok, course["id"])
    _complete_lecture(db, seed_org["student"].id, lec["id"])
    q = _add_exam_q(client, tok, course["id"], prompt="q", options=["a", "b", "c", "d"],
                    answer_indexes=[1]).json()

    stok = _student_token(client, seed_org)
    s1 = client.post(f"/api/v1/courses/{course['id']}/exam/session", headers=auth(stok)).json()
    # 4보기 → 2보기로 축소
    r = client.put(f"/api/v1/ops/courses/{course['id']}/exam-questions/{q['id']}",
                   json={"options": ["a", "b"], "answer_indexes": [0]}, headers=auth(tok))
    assert r.status_code == 200
    # 재발급은 크래시(IndexError 500) 없이 유효한 새 회차를 준다
    s2 = client.post(f"/api/v1/courses/{course['id']}/exam/session", headers=auth(stok))
    assert s2.status_code == 200, s2.text
    assert s2.json()["sitting_id"] != s1["sitting_id"]
    assert len(s2.json()["questions"][0]["options"]) == 2


def test_negative_and_out_of_range_picks_rejected(client, db, seed_org):
    """음수·범위 밖 picks는 무응답(오답)으로 — order[-1] 같은 파이썬 음수 인덱싱으로
    표시-순서 계약을 우회하는 것 차단(skeptic 경미 지적)."""
    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    lec = _assign_lecture(client, tok, course["id"])
    _complete_lecture(db, seed_org["student"].id, lec["id"])
    _add_exam_q(client, tok, course["id"], prompt="q", options=["a", "b", "c"], answer_indexes=[0])

    stok = _student_token(client, seed_org)
    sess = client.post(f"/api/v1/courses/{course['id']}/exam/session", headers=auth(stok)).json()
    qid = sess["questions"][0]["question_id"]
    # 음수·범위 밖 → 전부 버려져 무응답(오답)
    res = client.post(f"/api/v1/courses/{course['id']}/exam/submit",
                      json={"sitting_id": _ready(db, sess["sitting_id"]),
                            "answers": [{"question_id": qid, "picks": [-1, 99]}]},
                      headers=auth(stok))
    assert res.status_code == 200
    assert res.json()["correct"] == 0 and res.json()["results"][0]["picked"] == []


def test_courses_exam_summary_passed_at(client, db, seed_org):
    """'나의 기록' 수료 현황 원천 — GET /courses의 exam{} 요약. 수료 시 passed/perfect/
    passed_at을 담아, 화면이 수료 완료/진행 중/잠김을 한 곳에서 나눠 보여줄 수 있게 한다."""
    tok = _instructor(client, db)
    stok = _student_token(client, seed_org)
    course = _mk_course(client, tok, db, subject="수학", title="수학 개념완성")
    lec = _assign_lecture(client, tok, course["id"])
    _complete_lecture(db, seed_org["student"].id, lec["id"])
    for i in range(2):
        _add_exam_q(client, tok, course["id"], prompt=f"q{i}", options=["a", "b"], answer_indexes=[i % 2])

    # 수료 전 — has_exam·available·미수료
    rows = client.get("/api/v1/courses", headers=auth(stok)).json()
    ex = next(c for c in rows if c["id"] == course["id"])["exam"]
    assert ex["has_exam"] and ex["available"] and ex["passed"] is False and ex["passed_at"] is None

    # 완벽 통과 수료 → passed/perfect/passed_at 채워짐 + 시험 활동이 있으니 last_activity_at도
    res = _submit_all_correct(client, stok, course["id"], db)
    assert res["passed"] and res["perfect"]
    rows2 = client.get("/api/v1/courses", headers=auth(stok)).json()
    ex2 = next(c for c in rows2 if c["id"] == course["id"])["exam"]
    assert ex2["passed"] is True and ex2["perfect"] is True and ex2["passed_at"]
    assert ex2["last_activity_at"]  # 진행 중 칸 최신순 정렬 근거(제출로 시험 활동 생김)


def test_certificate_only_after_completion(client, db, seed_org):
    """수료증 데이터는 실제 수료한 학생만 받는다(위조 방지) — 미수료 404, 수료 후 200.

    수료증 이미지는 프론트가 그리지만 근거 데이터는 서버가 수료를 검증한 뒤에만 준다.
    학생 화면 규약대로 실명이 아니라 nickname을 싣고, 과목·수료일·문항수는 완료 스냅샷에서 온다."""
    tok = _instructor(client, db)
    stok = _student_token(client, seed_org)
    course = _mk_course(client, tok, db, subject="수학", title="수학 개념완성")
    lec = _assign_lecture(client, tok, course["id"])
    _complete_lecture(db, seed_org["student"].id, lec["id"])
    for i in range(2):
        _add_exam_q(client, tok, course["id"], prompt=f"q{i}", options=["a", "b"], answer_indexes=[i % 2])

    # 수료 전 → 404 (미수료 학생은 수료증 데이터를 못 받는다)
    r = client.get(f"/api/v1/courses/{course['id']}/exam/certificate", headers=auth(stok))
    assert r.status_code == 404

    # 완벽 통과 수료
    res = _submit_all_correct(client, stok, course["id"], db)
    assert res["passed"] and res["perfect"]

    # 수료 후 → 200, 서버가 검증한 데이터
    r2 = client.get(f"/api/v1/courses/{course['id']}/exam/certificate", headers=auth(stok))
    assert r2.status_code == 200, r2.text
    cert = r2.json()
    assert cert["course_title"] == "수학 개념완성" and cert["subject"] == "수학"
    assert cert["student_name"] == seed_org["student"].nickname  # 실명 아님(가명)
    assert cert["perfect"] is True and cert["question_count"] == 2
    assert cert["passed_at"] and cert["serial"].startswith("CATCHAP-")


def test_certificate_serial_stable(client, db, seed_org):
    """일련번호는 completion.id에서 결정적 파생 — 재발급(재호출)해도 같은 번호(위·변조 대조용)."""
    tok = _instructor(client, db)
    stok = _student_token(client, seed_org)
    course = _mk_course(client, tok, db)
    lec = _assign_lecture(client, tok, course["id"])
    _complete_lecture(db, seed_org["student"].id, lec["id"])
    _add_exam_q(client, tok, course["id"], prompt="q", options=["a", "b"], answer_indexes=[0])
    _submit_all_correct(client, stok, course["id"], db)

    a = client.get(f"/api/v1/courses/{course['id']}/exam/certificate", headers=auth(stok)).json()
    b = client.get(f"/api/v1/courses/{course['id']}/exam/certificate", headers=auth(stok)).json()
    assert a["serial"] == b["serial"] and len(a["serial"]) == len("CATCHAP-") + 12


def test_exam_stats_pass_rate_and_completion(client, db, seed_org):
    """시험 통계 — 문항별 통과율·오답 시도·코스 수료율(강사·운영자 대시보드 원천).

    한 문항을 일부러 틀렸다가 정복하면 그 문항의 wrong_attempts가 잡히고, 전 문항 정복 시
    수료율이 오른다. 통과율=정복 학생/시도 학생(아무도 안 풀면 None)."""
    tok = _instructor(client, db)
    stok = _student_token(client, seed_org)
    course = _mk_course(client, tok, db)
    lec = _assign_lecture(client, tok, course["id"])
    _complete_lecture(db, seed_org["student"].id, lec["id"])
    for i in range(2):
        _add_exam_q(client, tok, course["id"], prompt=f"q{i}", options=["a", "b", "c"], answer_indexes=[i % 3])

    # 통계(응시 전) — 응시 0, 통과율/수료율 None(0%로 오해 방지)
    st0 = client.get(f"/api/v1/ops/courses/{course['id']}/exam-stats", headers=auth(tok)).json()
    assert st0["attempted_students"] == 0 and st0["completions"] == 0
    assert st0["completion_rate"] is None
    assert all(q["pass_rate"] is None for q in st0["questions"])

    # 1회차: q0 정답, q1 오답
    sess = client.post(f"/api/v1/courses/{course['id']}/exam/session", headers=auth(stok)).json()
    answers, wrong_qid = [], None
    for idx, item in enumerate(sess["questions"]):
        q = db.get(CourseExamQuestion, item["question_id"])
        ct = {q.options[a] for a in q.answer_indexes}
        picks = [i for i, o in enumerate(item["options"]) if o in ct]
        if q.prompt == "q1":
            wrong_qid = q.id
            picks = [i for i, o in enumerate(item["options"]) if o not in ct][:1]
        answers.append({"question_id": q.id, "picks": picks})
    client.post(f"/api/v1/courses/{course['id']}/exam/submit",
                json={"sitting_id": _ready(db, sess["sitting_id"]), "answers": answers}, headers=auth(stok))
    # 2회차: 틀린 q1 정답 → 수료
    _submit_all_correct(client, stok, course["id"], db)

    st = client.get(f"/api/v1/ops/courses/{course['id']}/exam-stats", headers=auth(tok)).json()
    assert st["attempted_students"] == 1
    assert st["completions"] == 1 and st["completion_rate"] == 1.0
    byq = {q["prompt"]: q for q in st["questions"]}
    # q0: 첫 시도 정답 → 오답 0·통과율 1.0
    assert byq["q0"]["wrong_attempts"] == 0 and byq["q0"]["pass_rate"] == 1.0
    # q1: 한 번 틀리고 정복 → 오답 1·통과율 1.0(결국 맞힘)·시도 학생 1
    assert byq["q1"]["wrong_attempts"] == 1 and byq["q1"]["pass_rate"] == 1.0
    assert byq["q1"]["students_attempted"] == 1 and byq["q1"]["students_mastered"] == 1


def test_exam_stats_first_try_and_distractors(client, db, seed_org):
    """시험 전용 지표 — 첫 시도 정답률(난이도·변별 신호) + 오답 선택지 분석(어느 보기가 낚나).

    학생이 처음에 특정 오답 보기('c')를 고르고 틀린 뒤 정답으로 정복하면: 첫 시도 정답률 0,
    그 오답 보기의 wrong_picks=1, 정답 보기 is_answer=True."""
    tok = _instructor(client, db)
    stok = _student_token(client, seed_org)
    course = _mk_course(client, tok, db)
    lec = _assign_lecture(client, tok, course["id"])
    _complete_lecture(db, seed_org["student"].id, lec["id"])
    q = _add_exam_q(client, tok, course["id"], prompt="정답은 a", options=["a", "b", "c"],
                    answer_indexes=[0]).json()

    # 1회차: 일부러 'c'(원본 인덱스 2)를 골라 오답
    sess = client.post(f"/api/v1/courses/{course['id']}/exam/session", headers=auth(stok)).json()
    item = sess["questions"][0]
    c_display = next(i for i, o in enumerate(item["options"]) if o == "c")
    client.post(f"/api/v1/courses/{course['id']}/exam/submit",
                json={"sitting_id": _ready(db, sess["sitting_id"]),
                      "answers": [{"question_id": q["id"], "picks": [c_display]}]},
                headers=auth(stok))
    # 2회차: 정답 'a'로 정복
    _submit_all_correct(client, stok, course["id"], db)

    st = client.get(f"/api/v1/ops/courses/{course['id']}/exam-stats", headers=auth(tok)).json()
    qs = st["questions"][0]
    assert qs["students_attempted"] == 1
    assert qs["first_try_correct"] == 0 and qs["first_try_rate"] == 0.0  # 첫 시도 틀림
    assert qs["pass_rate"] == 1.0  # 결국 정복
    opts = {o["index"]: o for o in qs["options"]}
    assert opts[0]["is_answer"] is True and opts[0]["text"] == "a"
    assert opts[2]["text"] == "c" and opts[2]["wrong_picks"] == 1  # 'c'가 낚은 오답 1건
    assert opts[1]["wrong_picks"] == 0  # 아무도 안 고른 보기


def test_exam_stats_scope_other_instructor_404(client, db, seed_org):
    """통계도 코스 소유 스코프 — 남의 코스는 404(_get_ops_course 재사용)."""
    from app.core.security import hash_password
    from app.models import User

    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    other = User(email="inst3@t.dev", password_hash=hash_password("Password123!"),
                 name="다른강사", role="instructor",
                 email_verified_at=__import__("datetime").datetime.utcnow())
    db.add(other)
    db.commit()
    otok = client.post("/api/v1/auth/ops-login",
                       json={"email": "inst3@t.dev", "password": "Password123!"}).json()["access_token"]
    r = client.get(f"/api/v1/ops/courses/{course['id']}/exam-stats", headers=auth(otok))
    assert r.status_code == 404


def test_metrics_isolation_no_learning_attempt(client, db, seed_org):
    """지표 격리(설계 §7) — 시험 응답은 LearningAttempt에 안 쌓인다(정답률 오염 방지)."""
    from app.models import LearningAttempt

    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    lec = _assign_lecture(client, tok, course["id"])
    _complete_lecture(db, seed_org["student"].id, lec["id"])
    _add_exam_q(client, tok, course["id"], prompt="q", options=["a", "b"], answer_indexes=[0])

    stok = _student_token(client, seed_org)
    _submit_all_correct(client, stok, course["id"], db)
    # 시험 응답은 course_exam_attempts에만
    assert db.query(CourseExamAttempt).filter(
        CourseExamAttempt.student_id == seed_org["student"].id).count() >= 1
    assert db.query(LearningAttempt).filter(
        LearningAttempt.student_id == seed_org["student"].id).count() == 0


# ---------------------------------------------------- 2단계: 문항 채우기(to-exam · LLM 생성)
def _add_lecture_q(db, lecture_id, *, prompt, options, answer_index,
                   order_no=0, status="active", image=False):
    from app.models import LectureQuestion

    payload = {"prompt": prompt, "options": options, "explain": ""}
    if image:
        payload["prompt_image"] = "x.png"
    lq = LectureQuestion(
        lecture_id=lecture_id, position_sec=0, content_start_sec=None,
        payload=payload, answer_index=answer_index, source="llm",
        status=status, order_no=order_no,
    )
    db.add(lq)
    db.commit()
    return lq


def test_import_exam_from_lectures(client, db, seed_org, media_dir):
    """코스 강의의 active 확인 문항 → 시험 문항(origin=lecture·draft) 복사. draft 제외·멱등,
    ★이미지 문항도 가져오되 이미지 파일은 새 UUID로 복사(강의 문항 생명주기와 독립)."""
    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    lec = _assign_lecture(client, tok, course["id"])
    _add_lecture_q(db, lec["id"], prompt="1+1은?", options=["1", "2", "3", "4"], answer_index=1, order_no=1)
    img_lq = _add_lecture_q(db, lec["id"], prompt="이미지문항", options=["가", "나"], answer_index=0, order_no=2)
    _add_lecture_q(db, lec["id"], prompt="초안", options=["가", "나"], answer_index=0, order_no=3, status="draft")
    # 강의 이미지 문항에 실제 프롬프트 이미지 첨부(파일 생성) — 가져오기가 복사하는지 검증
    ai = _attach_image(client, tok, lec["id"], img_lq.id, slot="prompt")
    assert ai.status_code == 200, ai.text
    lec_img_id = _image_id_from_url(ai.json()["prompt_image_url"])

    r = client.post(
        f"/api/v1/ops/courses/{course['id']}/exam-questions/import-from-lectures", headers=auth(tok)
    )
    assert r.status_code == 200, r.text
    # 텍스트+이미지 두 active 문항 모두 가져옴. draft는 쿼리(status=active)에서 아예 빠져
    # skipped에 안 잡힌다(가져올 대상이 아니었던 것 ≠ 대상인데 스킵된 것).
    assert r.json() == {"imported": 2, "skipped": 0}

    qs = client.get(f"/api/v1/ops/courses/{course['id']}/exam-questions", headers=auth(tok)).json()
    assert len(qs) == 2 and all(q["origin"] == "lecture" and q["status"] == "draft" for q in qs)
    text_q = next(q for q in qs if q["prompt"] == "1+1은?")
    assert text_q["answer_indexes"] == [1] and "강의" in (text_q["source"] or "")
    assert text_q["prompt_image_url"] is None  # 텍스트 문항은 이미지 없음

    # 이미지 문항 — 복사됐고, 이미지 id가 강의 원본과 다르며(독립), 무인증 서빙 200
    img_q = next(q for q in qs if q["prompt"] == "이미지문항")
    assert img_q["prompt_image_url"], "이미지가 복사되지 않았다"
    assert _image_id_from_url(img_q["prompt_image_url"]) != lec_img_id, "원본을 공유(복사 안 됨)"
    assert client.get(img_q["prompt_image_url"]).status_code == 200

    # 멱등 — 재실행하면 이미 가져온 건 스킵(중복 안 생김)
    r2 = client.post(
        f"/api/v1/ops/courses/{course['id']}/exam-questions/import-from-lectures", headers=auth(tok)
    )
    assert r2.json()["imported"] == 0
    assert len(client.get(
        f"/api/v1/ops/courses/{course['id']}/exam-questions", headers=auth(tok)).json()) == 2


def _attach_exam_image(client, tok, course_id, qid, *, slot="prompt", option_index=None,
                       filename="캡처.png", content_type="image/png", size=2048):
    data = {"slot": slot}
    if option_index is not None:
        data["option_index"] = str(option_index)
    return client.post(
        f"/api/v1/ops/courses/{course_id}/exam-questions/{qid}/images",
        data=data, files={"file": (filename, b"\x02" * size, content_type)}, headers=auth(tok),
    )


def test_exam_question_image_attach_serve_delete(client, db, seed_org, media_dir):
    """시험 문항 이미지 첨부 → 무인증 서빙 200 → 삭제 후 404. 학생 응시 화면에 URL이 실린다."""
    tok = _instructor(client, db)
    stok = _student_token(client, seed_org)
    course = _mk_course(client, tok, db)
    lec = _assign_lecture(client, tok, course["id"])
    _complete_lecture(db, seed_org["student"].id, lec["id"])
    q = _add_exam_q(client, tok, course["id"], prompt="이 사진은?", options=["가", "나"],
                    answer_indexes=[0]).json()

    # 프롬프트 이미지 첨부 → row에 URL, 파일 서빙 200(무인증 — <img>가 로드)
    r = _attach_exam_image(client, tok, course["id"], q["id"], slot="prompt")
    assert r.status_code == 200, r.text
    url = r.json()["prompt_image_url"]
    assert url and client.get(url).status_code == 200
    # 보기 이미지도 첨부
    r2 = _attach_exam_image(client, tok, course["id"], q["id"], slot="option", option_index=1)
    assert r2.status_code == 200 and r2.json()["option_image_urls"][1]

    # svg·잘못된 확장자는 400(인라인 렌더 안전 — 래스터만)
    bad = _attach_exam_image(client, tok, course["id"], q["id"], filename="x.svg",
                             content_type="image/svg+xml")
    assert bad.status_code == 400

    # 학생 응시 화면(session)에 이미지 URL이 보기 순서에 맞춰 실린다
    sess = client.post(f"/api/v1/courses/{course['id']}/exam/session", headers=auth(stok)).json()
    sq = sess["questions"][0]
    assert sq["prompt_image_url"] and len(sq["option_image_urls"]) == 2
    assert any(u for u in sq["option_image_urls"])  # 보기 이미지 하나는 실려 있음

    # 삭제 → images 참조 제거 + 파일 물리 삭제(서빙 404)
    d = client.delete(
        f"/api/v1/ops/courses/{course['id']}/exam-questions/{q['id']}/images?slot=prompt",
        headers=auth(tok),
    )
    assert d.status_code == 200 and d.json()["prompt_image_url"] is None
    assert client.get(url).status_code == 404


def test_generate_exam_questions_llm(client, db, seed_org, monkeypatch):
    """LLM 코스 시험 문항 생성 — 코스 제목·과목·강의를 생성기에 넘기고 origin=llm·draft로 저장."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ANTHROPIC_API_KEY", "sk-x")
    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    _assign_lecture(client, tok, course["id"], title="1강 분수")

    import app.clients.ai_client as ai

    seen = {}

    def fake_gen(**kwargs):
        seen.update(kwargs)
        return [
            {"prompt": "분수 개념?", "options": ["가", "나", "다", "라"], "answer_index": 2, "explain": "해설"},
            {"prompt": "약분?", "options": ["가", "나", "다", "라"], "answer_index": 0, "explain": ""},
        ]

    monkeypatch.setattr(ai, "generate_course_exam_questions", fake_gen)
    r = client.post(
        f"/api/v1/ops/courses/{course['id']}/exam-questions/generate",
        json={"n": 2}, headers=auth(tok),
    )
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 2
    # 코스 맥락이 생성기에 전달됐는지
    assert seen["course_title"] == "수학 개념완성" and seen["subject"] == "수학"
    assert any(l["title"] == "1강 분수" for l in seen["lectures"])
    # 저장 = origin llm·draft·answer_indexes=[answer_index]
    qs = client.get(f"/api/v1/ops/courses/{course['id']}/exam-questions", headers=auth(tok)).json()
    assert len(qs) == 2 and all(q["origin"] == "llm" and q["status"] == "draft" for q in qs)
    assert qs[0]["answer_indexes"] == [2]


def test_generate_exam_uses_lecture_transcript(client, db, seed_org, monkeypatch):
    """강의 자막이 있으면 코스 시험 생성기에 그 텍스트가 전달되고 used_transcripts에 반영된다."""
    from app.core.config import get_settings
    from app.models import LectureTranscript

    monkeypatch.setattr(get_settings(), "ANTHROPIC_API_KEY", "sk-x")
    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    lec = _assign_lecture(client, tok, course["id"], title="1강")
    db.add(LectureTranscript(
        lecture_id=lec["id"],
        segments=[{"start": 0.0, "end": 3.0, "text": "분수는 전체를 나눈 조각이다"}],
        source="paste", segment_count=1,
    ))
    db.commit()

    import app.clients.ai_client as ai

    seen = {}

    def fake_gen(**k):
        seen.update(k)
        return [{"prompt": "q", "options": ["가", "나", "다", "라"], "answer_index": 0, "explain": ""}]

    monkeypatch.setattr(ai, "generate_course_exam_questions", fake_gen)
    r = client.post(
        f"/api/v1/ops/courses/{course['id']}/exam-questions/generate", json={"n": 1}, headers=auth(tok)
    )
    assert r.status_code == 200, r.text
    assert r.json()["used_transcripts"] == 1
    # 생성기에 전달된 lectures에 자막 텍스트가 실려 있는지
    assert any("분수는 전체를 나눈" in (l.get("transcript") or "") for l in seen["lectures"])
    # 자막이 있으면 프롬프트가 '자막 근거' 모드로 바뀌는지(직접 확인)
    from app.clients.ai_client import _course_exam_prompt
    p = _course_exam_prompt("코스", "수학", seen["lectures"], 1)
    assert "실제 내용(자막)" in p and "분수는 전체를 나눈" in p


def test_generate_exam_questions_no_key_503(client, db, seed_org, monkeypatch):
    """키가 하나도 없으면 정직한 503(stub 문항 생성 금지)."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(get_settings(), "OPENAI_API_KEY", "")
    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    r = client.post(
        f"/api/v1/ops/courses/{course['id']}/exam-questions/generate",
        json={"n": 2}, headers=auth(tok),
    )
    assert r.status_code == 503


# ---------------------------------------------------------------- 강사 홈 대시보드
def _draft_lecture_q(db, lecture_id):
    from app.models import LectureQuestion

    db.add(LectureQuestion(
        lecture_id=lecture_id, payload={"prompt": "검수대기", "options": ["a", "b"]},
        answer_index=0, status="draft",
    ))
    db.commit()


def test_instructor_dashboard_counts_and_drafts(client, db, seed_org):
    """강사 홈 — 내 강의/코스 수 + 검수 대기(draft) 문항을 강의 가로질러 합산·나열."""
    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    lec = _assign_lecture(client, tok, course["id"])
    _draft_lecture_q(db, lec["id"])  # 강의 draft 확인문항 1
    _add_exam_q(client, tok, course["id"], prompt="dq", options=["a", "b"],
                answer_indexes=[0], status="draft")  # 시험 draft 1

    d = client.get("/api/v1/ops/instructor/dashboard", headers=auth(tok)).json()
    assert d["lecture_count"] == 1 and d["course_count"] == 1
    assert d["draft_lecture_questions"] == 1
    assert d["draft_exam_questions"] == 1
    assert d["draft_question_count"] == 2
    # 강의별 검수 대기 목록(바로가기) — 강의가 여럿이어도 한곳에서 보게
    assert len(d["draft_by_lecture"]) == 1
    assert d["draft_by_lecture"][0]["lecture_id"] == lec["id"]
    assert d["draft_by_lecture"][0]["draft_count"] == 1
    # 활성 확인문항 0개 강의 = 시청 검증 없음 경고
    assert d["lectures_without_checkpoint"] == 1


def test_instructor_dashboard_scope_isolated(client, db, seed_org):
    """다른 강사의 강의/코스/검수대기는 내 대시보드에 절대 잡히지 않는다."""
    from datetime import datetime

    from app.core.security import hash_password
    from app.models import User

    tok = _instructor(client, db)
    # 남의 강사 + 그의 코스·강의·draft
    db.add(User(email="inst9@t.dev", password_hash=hash_password("Password123!"),
                name="남강사", role="instructor", status="active",
                email_verified_at=datetime.utcnow()))
    db.commit()
    otok = client.post("/api/v1/auth/ops-login",
                       json={"email": "inst9@t.dev", "password": "Password123!"}).json()["access_token"]
    ocourse = _mk_course(client, otok, db, title="남코스")
    olec = _assign_lecture(client, otok, ocourse["id"], title="남1강")
    _draft_lecture_q(db, olec["id"])

    # 내 대시보드는 전부 0 (남의 것 미포함)
    d = client.get("/api/v1/ops/instructor/dashboard", headers=auth(tok)).json()
    assert d["lecture_count"] == 0 and d["course_count"] == 0
    assert d["draft_question_count"] == 0 and d["draft_by_lecture"] == []


def test_instructor_dashboard_weak_questions_ranked(client, db, seed_org):
    """이해도(약한 대목) — 내 코스 활성 시험문항을 통과율 낮은 순 Top으로 정렬."""
    from app.models import CourseExamAttempt

    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    easy = _add_exam_q(client, tok, course["id"], prompt="쉬움", options=["a", "b"],
                       answer_indexes=[0]).json()
    hard = _add_exam_q(client, tok, course["id"], prompt="어려움", options=["a", "b"],
                       answer_indexes=[0]).json()
    # easy: 2명 시도·2명 정복(통과율 1.0) / hard: 2명 시도·0명 정복(통과율 0.0)
    for sid in ("s1", "s2"):
        db.add(CourseExamAttempt(student_id=sid, course_id=course["id"],
                                 question_id=easy["id"], sitting_id="x", result="correct"))
        db.add(CourseExamAttempt(student_id=sid, course_id=course["id"],
                                 question_id=hard["id"], sitting_id="x", result="incorrect"))
    db.commit()

    d = client.get("/api/v1/ops/instructor/dashboard", headers=auth(tok)).json()
    wq = d["weak_questions"]
    assert len(wq) == 2
    assert wq[0]["question_id"] == hard["id"] and wq[0]["pass_rate"] == 0.0  # 가장 약한 게 먼저
    assert wq[1]["question_id"] == easy["id"] and wq[1]["pass_rate"] == 1.0


def test_instructor_dashboard_weak_lectures(client, db, seed_org):
    """강의별 확인문항 통과율 — 강의마다 pass/fail 이벤트로 통과율 산출, 낮은 순 정렬."""
    from app.models import LectureCheckpointEvent

    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    easy = _assign_lecture(client, tok, course["id"], title="쉬운 강의")
    hard = _assign_lecture(client, tok, course["id"], title="어려운 강의")
    # 쉬운: 3통과/0실패=100% · 어려운: 1통과/3실패=25%
    for i in range(3):
        db.add(LectureCheckpointEvent(student_id=f"s{i}", lecture_id=easy["id"], position_sec=10, result="passed"))
    db.add(LectureCheckpointEvent(student_id="s0", lecture_id=hard["id"], position_sec=10, result="passed"))
    for i in range(3):
        db.add(LectureCheckpointEvent(student_id=f"s{i}", lecture_id=hard["id"], position_sec=10, result="failed"))
    db.commit()

    wl = client.get("/api/v1/ops/instructor/dashboard", headers=auth(tok)).json()["weak_lectures"]
    assert len(wl) == 2
    assert wl[0]["lecture_id"] == hard["id"] and wl[0]["pass_rate"] == 0.25  # 어려운 강의 먼저
    assert wl[0]["attempts"] == 4 and wl[0]["learners"] == 3
    assert wl[1]["lecture_id"] == easy["id"] and wl[1]["pass_rate"] == 1.0


def test_instructor_dashboard_weak_checkpoint_questions(client, db, seed_org):
    """문항별 확인문항 — 특정 문항의 통과율·검토 권장(매우 낮음+충분 시도)."""
    from app.models import LectureCheckpointEvent, LectureQuestion

    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    lec = _assign_lecture(client, tok, course["id"])
    q_hard = LectureQuestion(lecture_id=lec["id"], payload={"prompt": "이상한 문항", "options": ["a", "b"]},
                             answer_index=0, status="active", position_sec=30)
    q_easy = LectureQuestion(lecture_id=lec["id"], payload={"prompt": "쉬운 문항", "options": ["a", "b"]},
                             answer_index=0, status="active", position_sec=60)
    db.add_all([q_hard, q_easy])
    db.commit()
    # 어려운: 1통과/4실패=20%(검토 권장) · 쉬운: 4통과/1실패=80%
    for i in range(4):
        db.add(LectureCheckpointEvent(student_id=f"s{i}", lecture_id=lec["id"], question_id=q_hard.id, position_sec=30, result="failed"))
    db.add(LectureCheckpointEvent(student_id="s4", lecture_id=lec["id"], question_id=q_hard.id, position_sec=30, result="passed"))
    for i in range(4):
        db.add(LectureCheckpointEvent(student_id=f"s{i}", lecture_id=lec["id"], question_id=q_easy.id, position_sec=60, result="passed"))
    db.add(LectureCheckpointEvent(student_id="s4", lecture_id=lec["id"], question_id=q_easy.id, position_sec=60, result="failed"))
    db.commit()

    cq = client.get("/api/v1/ops/instructor/dashboard", headers=auth(tok)).json()["weak_checkpoint_questions"]
    assert len(cq) == 2
    assert cq[0]["question_id"] == q_hard.id and cq[0]["pass_rate"] == 0.2 and cq[0]["review"] is True
    assert cq[0]["prompt"] == "이상한 문항"
    assert cq[1]["question_id"] == q_easy.id and cq[1]["pass_rate"] == 0.8 and cq[1]["review"] is False


# ================= 자동화 방어 (쿨다운·레이트리밋·최소 풀이 시간) =================
# mastery 는 '정답 1건 = 영구 정복'이라 되돌림이 없다. 시도 비용이 0이면 무작위 제출을
# 반복하는 것만으로 수료가 된다. 아래는 그 비용을 올리는 세 장치를 각각 검증하고,
# 마지막에 공격 시나리오 자체를 재현해 실제로 막히는지 본다.


def _exam_ready_course(client, db, seed_org, *, n_questions=3):
    """응시 가능한 상태(강의 완주 + 문항 n개)를 만들고 (강사토큰, 코스, 학생토큰) 반환."""
    tok = _instructor(client, db)
    course = _mk_course(client, tok, db)
    lec = _assign_lecture(client, tok, course["id"])
    _complete_lecture(db, seed_org["student"].id, lec["id"])
    for i in range(n_questions):
        _add_exam_q(client, tok, course["id"], prompt=f"q{i}",
                    options=["a", "b", "c", "d"], answer_indexes=[i % 4])
    return tok, course, _student_token(client, seed_org)


def _answer_all_wrong(db, sess):
    """세션 문항 → 전부 오답인 picks."""
    out = []
    for item in sess["questions"]:
        q = db.get(CourseExamQuestion, item["question_id"])
        ct = {q.options[i] for i in q.answer_indexes}
        out.append({
            "question_id": q.id,
            "picks": [i for i, o in enumerate(item["options"]) if o not in ct][:1],
        })
    return out


# ---- 오답 쿨다운
def test_wrong_answer_is_not_reserved_during_cooldown(client, db, seed_org):
    """틀린 문항은 쿨다운 동안 재출제되지 않는다 — 이게 무작위 반복의 비용을 올린다."""
    _tok, course, stok = _exam_ready_course(client, db, seed_org, n_questions=3)
    url = f"/api/v1/courses/{course['id']}"
    sess = client.post(f"{url}/exam/session", headers=auth(stok)).json()
    wrong_ids = {a["question_id"] for a in _answer_all_wrong(db, sess)}
    client.post(f"{url}/exam/submit",
                json={"sitting_id": _ready(db, sess["sitting_id"]),
                      "answers": _answer_all_wrong(db, sess)}, headers=auth(stok))

    cd = client.post(f"{url}/exam/session", headers=auth(stok)).json()
    assert cd["cooldown"] is True
    assert "sitting_id" not in cd  # 빈 회차를 주지 않는다
    assert 0 < cd["retry_after_sec"] <= cd["cooldown_minutes"] * 60

    # 쿨다운 만료 → 전부 다시 나온다(영구 차단이 아니다)
    _cool_off(db, course["id"])
    again = client.post(f"{url}/exam/session", headers=auth(stok)).json()
    assert {q["question_id"] for q in again["questions"]} == wrong_ids


def test_cooldown_only_blocks_wrong_ones_not_untouched(client, db, seed_org):
    """쿨다운은 틀린 문항에만 걸린다 — 안 푼 문항은 그대로 나와서 학습이 멈추지 않는다."""
    tok, course, stok = _exam_ready_course(client, db, seed_org, n_questions=3)
    # 문항을 더 넣어 회차(10) 밖의 미출제분을 만든다
    for i in range(3, 12):
        _add_exam_q(client, tok, course["id"], prompt=f"q{i}",
                    options=["a", "b", "c", "d"], answer_indexes=[i % 4])
    url = f"/api/v1/courses/{course['id']}"
    sess = client.post(f"{url}/exam/session", headers=auth(stok)).json()
    wrong_ids = {a["question_id"] for a in _answer_all_wrong(db, sess)}
    client.post(f"{url}/exam/submit",
                json={"sitting_id": _ready(db, sess["sitting_id"]),
                      "answers": _answer_all_wrong(db, sess)}, headers=auth(stok))

    nxt = client.post(f"{url}/exam/session", headers=auth(stok)).json()
    served = {q["question_id"] for q in nxt["questions"]}
    assert served, "안 푼 문항이 남았으면 회차가 나와야 한다"
    assert not (served & wrong_ids), "쿨다운 중인 문항이 섞이면 안 된다"


def test_perfect_challenge_is_exempt_from_cooldown(client, db, seed_org):
    """완벽 도전은 쿨다운 면제 — 수료 후 재도전 전용이고 전 문항을 한 판에 다 맞혀야 해
    무작위로는 사실상 불가능하다(구조가 자기제한적). 걸면 정당한 재도전자만 불편해진다."""
    _tok, course, stok = _exam_ready_course(client, db, seed_org, n_questions=3)
    url = f"/api/v1/courses/{course['id']}"
    # 완벽 도전은 '수료했지만 perfect 는 아닌' 상태에서만 열린다. perfect 판정은 오답
    # 이력이 아니라 '한 회차에 전 문항 정복'이므로, 마지막 회차가 일부만 담기게 만든다 —
    # 1회차에 하나만 맞히고, 2회차에 나머지 둘을 맞히면 어느 회차도 전 문항 커버가 아니다.
    first = client.post(f"{url}/exam/session", headers=auth(stok)).json()
    answers = []
    for idx, item in enumerate(first["questions"]):
        q = db.get(CourseExamQuestion, item["question_id"])
        ct = {q.options[i] for i in q.answer_indexes}
        if idx == 0:
            picks = [i for i, o in enumerate(item["options"]) if o in ct]
        else:
            picks = [i for i, o in enumerate(item["options"]) if o not in ct][:1]
        answers.append({"question_id": q.id, "picks": picks})
    client.post(f"{url}/exam/submit",
                json={"sitting_id": _ready(db, first["sitting_id"]), "answers": answers},
                headers=auth(stok))
    done = _submit_all_correct(client, stok, course["id"], db)
    assert done["passed"] is True and done["perfect"] is False

    ch = client.post(f"{url}/exam/session?perfect=true", headers=auth(stok)).json()
    assert "sitting_id" in ch, ch
    client.post(f"{url}/exam/submit",
                json={"sitting_id": _ready(db, ch["sitting_id"]),
                      "answers": _answer_all_wrong(db, ch)}, headers=auth(stok))
    # 방금 전 문항을 틀렸는데도 곧바로 다시 도전할 수 있어야 한다
    again = client.post(f"{url}/exam/session?perfect=true", headers=auth(stok)).json()
    assert again.get("cooldown") is not True
    assert len(again["questions"]) == 3


# ---- 응시 레이트리밋
def test_session_rate_limited_per_hour(client, db, seed_org):
    """회차 발급 상한 — 쿨다운이 못 막는 '미정복이 회차 크기보다 많은' 초반 구간을 막는다."""
    from app.api.v1.endpoints.course_exam import RATE_EXAM_SESSION_PER_HOUR as LIMIT

    _tok, course, stok = _exam_ready_course(client, db, seed_org, n_questions=2)
    url = f"/api/v1/courses/{course['id']}/exam/session"
    codes = [client.post(url, headers=auth(stok)).status_code for _ in range(LIMIT + 2)]
    assert codes[:LIMIT] == [200] * LIMIT, "정상 범위는 통과해야 한다"
    assert codes[LIMIT] == 429 and codes[LIMIT + 1] == 429


def test_rate_limit_is_per_student(client, db, seed_org):
    """한 학생이 상한을 소진해도 다른 학생은 영향받지 않는다."""
    from app.api.v1.endpoints.course_exam import RATE_EXAM_SESSION_PER_HOUR as LIMIT
    from app.core.security import hash_password
    from app.models import Lecture, StudentProfile

    _tok, course, stok = _exam_ready_course(client, db, seed_org, n_questions=2)
    url = f"/api/v1/courses/{course['id']}/exam/session"
    for _ in range(LIMIT + 1):
        client.post(url, headers=auth(stok))
    assert client.post(url, headers=auth(stok)).status_code == 429

    other = StudentProfile(
        organization_id=seed_org["student"].organization_id,
        class_id=seed_org["student"].class_id,
        student_login_id="cooldown-other", student_code="CAT-CD-01",
        password_hash=hash_password("Password123!"), nickname="다른학생", grade_band="adult",
    )
    db.add(other)
    db.commit()
    for lec in db.query(Lecture).filter_by(course_id=course["id"]).all():
        _complete_lecture(db, other.id, lec.id)
    otok = client.post("/api/v1/auth/student-login",
                       json={"student_login_id": "cooldown-other",
                             "password": "Password123!"}).json()["access_token"]
    assert client.post(url, headers=auth(otok)).status_code == 200


# ---- 최소 풀이 시간 (서버 시각 기준)
def test_instant_submit_rejected_and_client_time_cannot_forge_it(client, db, seed_org):
    """읽지도 않고 제출하면 429. 클라이언트가 solve_time_ms 를 부풀려도 소용없다 —
    서버가 회차 발급 시각으로 직접 잰다."""
    _tok, course, stok = _exam_ready_course(client, db, seed_org, n_questions=3)
    url = f"/api/v1/courses/{course['id']}"
    sess = client.post(f"{url}/exam/session", headers=auth(stok)).json()

    r = client.post(f"{url}/exam/submit",
                    json={"sitting_id": sess["sitting_id"],
                          "answers": _answer_all_correct(db, sess),
                          "solve_time_ms": 9_999_999},  # 위조 시도
                    headers=auth(stok))
    assert r.status_code == 429
    assert r.json()["detail"]["retry_after_sec"] > 0

    # 실제로 시간이 흐른 뒤에는 통과
    ok = client.post(f"{url}/exam/submit",
                     json={"sitting_id": _ready(db, sess["sitting_id"]),
                           "answers": _answer_all_correct(db, sess)},
                     headers=auth(stok))
    assert ok.status_code == 200


def test_solve_time_is_server_measured_not_client_reported(client, db, seed_org):
    """저장되는 풀이 시간은 서버 계산값이다 — 종전엔 클라이언트 값을 그대로 믿어
    운영 통계의 '평균 풀이 시간'을 위조할 수 있었다."""
    _tok, course, stok = _exam_ready_course(client, db, seed_org, n_questions=2)
    url = f"/api/v1/courses/{course['id']}"
    sess = client.post(f"{url}/exam/session", headers=auth(stok)).json()
    client.post(f"{url}/exam/submit",
                json={"sitting_id": _ready(db, sess["sitting_id"], seconds=120),
                      "answers": _answer_all_correct(db, sess),
                      "solve_time_ms": 1},  # 클라이언트는 1ms 라고 주장
                headers=auth(stok))
    saved = db.query(CourseExamAttempt).filter(
        CourseExamAttempt.course_id == course["id"]).all()
    assert saved
    # 실제 경과(120초)/문항수(2) = 60초. 클라이언트가 말한 1ms 가 아니다.
    for a in saved:
        assert a.solve_time_ms > 1_000, a.solve_time_ms


# ---- 공격 시나리오 — 실제로 막히는지
def test_random_guessing_cannot_farm_completion(client, db, seed_org):
    """무작위 제출 반복으로 수료가 되지 않는다.

    방어가 없으면 '4지선다 × 영구 정복 × 시도 비용 0' 이라 반복만으로 확률 1에 수렴한다.
    여기서는 방어를 켠 채 같은 공격을 돌려서, 수료 전에 쿨다운/레이트리밋에 막히는지 본다.
    누가 방어를 되돌리면 이 테스트가 잡는다.
    """
    import random as _r

    _tok, course, stok = _exam_ready_course(client, db, seed_org, n_questions=8)
    url = f"/api/v1/courses/{course['id']}"
    _r.seed(1234)  # 재현 가능하게

    blocked = None
    for _ in range(60):  # 방어가 없으면 8문항은 이 안에 충분히 정복된다
        s = client.post(f"{url}/exam/session", headers=auth(stok))
        if s.status_code == 429:
            blocked = "rate_limit"
            break
        body = s.json()
        if body.get("passed"):
            break
        if body.get("cooldown"):
            blocked = "cooldown"
            break
        answers = [{"question_id": q["question_id"], "picks": [_r.randrange(len(q["options"]))]}
                   for q in body["questions"]]
        sub = client.post(f"{url}/exam/submit",
                          json={"sitting_id": _ready(db, body["sitting_id"]), "answers": answers},
                          headers=auth(stok))
        if sub.status_code == 429:
            blocked = "rate_limit"
            break

    assert blocked in ("cooldown", "rate_limit"), f"방어에 막히지 않았다: {blocked}"
    st = client.get(f"{url}/exam", headers=auth(stok)).json()
    assert st["passed"] is False, "무작위 제출로 수료가 되면 안 된다"
