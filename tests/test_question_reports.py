"""학생 확인문항 신고 기능 — POST /captcha/v1/report + 강사 검토(GET/PATCH).

신고 대상 문항은 학생 화면에 없는 question_id를 '학생이 받은 챌린지 토큰'에서 서버가
복원해 확정한다(위조·타 문항 신고 차단). 학생 신원은 강사 응답에 노출하지 않는다(PII).
"""

import time

from app.core.security import create_access_token, hash_password
from app.models import (
    ApiKey,
    Lecture,
    LectureQuestion,
    LectureQuestionReport,
    Notification,
    Organization,
    Site,
    StudentProfile,
    User,
)
from app.services import captcha_service as cs

CAP = "/api/v1/captcha/v1"


def _setup(db, *, first_party=True):
    """edu 1st-party 키 + 강사 강의/문항 + 학생 + 유효 챌린지 토큰 일습."""
    org = Organization(name="햇살초", code="HS-1")
    db.add(org); db.flush()
    site = Site(organization_id=org.id, name="site", domain="ex.kr", allowed_origins=[])
    db.add(site); db.flush()
    key = ApiKey(
        organization_id=org.id, site_id=site.id, site_key="ck_edu_test",
        secret_key_hash="x", product="edu", subject="영어",
        first_party=first_party, status="active",
    )
    db.add(key)
    inst = User(email="teach@t.dev", password_hash=hash_password("Password123!"),
                name="강사", role="instructor")
    db.add(inst); db.flush()
    lec = Lecture(title="영어 1강", subject="영어", video_ext=".mp4",
                  uploaded_by=inst.id, status="active", duration_sec=600)
    db.add(lec); db.flush()
    q = LectureQuestion(lecture_id=lec.id, position_sec=30, answer_index=0,
                        payload={"prompt": "무엇이 정답일까요?", "options": ["A", "B"]})
    db.add(q); db.flush()
    stu = StudentProfile(student_login_id="child@t.dev", student_code="CAT-1",
                         password_hash=hash_password("Password123!"), nickname="하은")
    db.add(stu); db.flush()
    db.commit()
    token = create_access_token(stu.id, "student")
    ch = cs._wrap("select_all", ["0"], {},
                  {"subj": "영어", "lec": lec.id, "qid": q.id, "cp": 30, "bank": True})["challenge_token"]
    return {"org": org, "key": key, "inst": inst, "lec": lec, "q": q, "stu": stu,
            "token": token, "ch": ch}


def _hdr(s):
    return {"X-Site-Key": s["key"].site_key, "Authorization": f"Bearer {s['token']}"}


# ---- peek_lecture_meta (순수 단위) --------------------------------------------
def test_peek_lecture_meta_valid():
    tok = cs._wrap("select_all", ["0"], {}, {"lec": "L1", "qid": "Q1", "cp": 30})["challenge_token"]
    meta = cs.peek_lecture_meta(tok)
    assert meta == {"lec": "L1", "qid": "Q1", "cp": 30}


def test_peek_lecture_meta_non_lecture_token():
    tok = cs._wrap("text", "answer", {}, {"subj": "수학", "qid": "Q1"})["challenge_token"]  # lec 없음
    assert cs.peek_lecture_meta(tok) is None


def test_peek_lecture_meta_forged():
    assert cs.peek_lecture_meta("not-a-real-token") is None


# ---- POST /report (학생) ------------------------------------------------------
def test_report_creates_row_and_notifies_instructor(client, db):
    s = _setup(db)
    r = client.post(f"{CAP}/report", headers=_hdr(s),
                    json={"challenge_token": s["ch"], "reason": "wrong_answer", "detail": "정답이 없어요"})
    assert r.status_code == 201, r.text
    row = db.query(LectureQuestionReport).filter_by(question_id=s["q"].id).first()
    assert row is not None and row.status == "open" and row.reason == "wrong_answer"
    assert row.student_id == s["stu"].id
    # 강사에게 in-app 알림 1건
    note = db.query(Notification).filter_by(user_id=s["inst"].id, type="question_report").first()
    assert note is not None


def test_report_duplicate_same_student_question_409(client, db):
    s = _setup(db)
    first = client.post(f"{CAP}/report", headers=_hdr(s),
                        json={"challenge_token": s["ch"], "reason": "typo"})
    assert first.status_code == 201
    dup = client.post(f"{CAP}/report", headers=_hdr(s),
                      json={"challenge_token": s["ch"], "reason": "unclear"})
    assert dup.status_code == 409


def test_report_forged_token_400(client, db):
    s = _setup(db)
    r = client.post(f"{CAP}/report", headers=_hdr(s),
                    json={"challenge_token": "garbage", "reason": "typo"})
    assert r.status_code == 400


def test_report_bad_reason_400(client, db):
    s = _setup(db)
    r = client.post(f"{CAP}/report", headers=_hdr(s),
                    json={"challenge_token": s["ch"], "reason": "해킹"})
    assert r.status_code == 400


def test_report_requires_student_auth_401(client, db):
    s = _setup(db)
    r = client.post(f"{CAP}/report", headers={"X-Site-Key": s["key"].site_key},
                    json={"challenge_token": s["ch"], "reason": "typo"})
    assert r.status_code == 401


# ---- 강사 검토 (GET / PATCH) --------------------------------------------------
def _instructor_token(s):
    return {"Authorization": f"Bearer {create_access_token(s['inst'].id, 'instructor')}"}


def test_instructor_lists_reports_without_student_pii(client, db):
    s = _setup(db)
    client.post(f"{CAP}/report", headers=_hdr(s),
                json={"challenge_token": s["ch"], "reason": "wrong_answer"})
    r = client.get(f"/api/v1/ops/lectures/{s['lec'].id}/reports", headers=_instructor_token(s))
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["reports"]) == 1
    rep = body["reports"][0]
    assert rep["reason"] == "wrong_answer"
    assert rep["question_prompt"] == "무엇이 정답일까요?"
    assert "student_id" not in rep  # PII 비노출
    assert body["counts_by_question"][s["q"].id] == 1


def test_instructor_cannot_see_others_lecture_reports_404(client, db):
    s = _setup(db)
    other = User(email="other@t.dev", password_hash=hash_password("Password123!"),
                 name="남", role="instructor")
    db.add(other); db.commit()
    tok = {"Authorization": f"Bearer {create_access_token(other.id, 'instructor')}"}
    r = client.get(f"/api/v1/ops/lectures/{s['lec'].id}/reports", headers=tok)
    assert r.status_code == 404


def test_instructor_resolves_report(client, db):
    s = _setup(db)
    client.post(f"{CAP}/report", headers=_hdr(s),
                json={"challenge_token": s["ch"], "reason": "typo"})
    rid = db.query(LectureQuestionReport).first().id
    r = client.patch(f"/api/v1/ops/lectures/{s['lec'].id}/reports/{rid}",
                     headers=_instructor_token(s), json={"status": "resolved"})
    assert r.status_code == 200, r.text
    row = db.get(LectureQuestionReport, rid)
    assert row.status == "resolved" and row.resolved_by == s["inst"].id and row.resolved_at is not None
    # 기본 목록(open)에서는 이제 안 보임
    open_list = client.get(f"/api/v1/ops/lectures/{s['lec'].id}/reports", headers=_instructor_token(s)).json()
    assert len(open_list["reports"]) == 0
