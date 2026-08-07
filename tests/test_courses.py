"""강사 코스(1단계) — CRUD·과목 고정·강사 소유 스코프·강의 연결·기존 강의 호환.

설계: docs/product-direction.md(코스=과목 고정), 강사 스코프는 test_instructor와 동일 규약.
"""

from tests.test_captcha_api import _instructor, _ops, auth
from tests.test_instructor import _instructor_ready
from tests.test_lectures import _student_token, _upload_lecture, media_dir  # noqa: F401 (fixture 재사용)


def _create_course(client, tok, *, title="수학 기초반", subject="수학"):
    r = client.post("/api/v1/ops/courses", json={"title": title, "subject": subject}, headers=auth(tok))
    assert r.status_code == 200, r.text
    return r.json()


def test_course_crud_and_subject_fixed(client, db, media_dir):
    ops = _instructor(client, db)
    c = _create_course(client, ops, title="수학 기초반", subject="수학")
    assert c["subject"] == "수학" and c["lecture_count"] == 0

    # 지원하지 않는 과목 400
    assert client.post("/api/v1/ops/courses", json={"title": "x", "subject": "체육"}, headers=auth(ops)).status_code == 400

    # 수정 — 제목/순서/상태. subject는 스키마에 없어 바뀌지 않는다(코스=과목 고정)
    r = client.put(f"/api/v1/ops/courses/{c['id']}", json={"title": "수학 심화반", "order_no": 5}, headers=auth(ops))
    assert r.status_code == 200 and r.json()["title"] == "수학 심화반" and r.json()["subject"] == "수학"

    # 소프트 삭제 — 목록에서 사라짐
    assert client.delete(f"/api/v1/ops/courses/{c['id']}", headers=auth(ops)).status_code == 200
    assert all(x["id"] != c["id"] for x in client.get("/api/v1/ops/courses", headers=auth(ops)).json())


def test_course_scope_instructor_owns_only(client, db, media_dir):
    """강사는 자기 코스만 — 목록 필터 + 남의 코스는 수정/삭제/강의연결 전부 404."""
    ops = _ops(client, db)
    created, itok = _instructor_ready(client, ops, email="c-inst@catchap.dev")

    # '남의 코스'는 다른 강사가 만든다 — 운영자는 저작하지 않으므로(감독·검수만, 0720).
    other, otok = _instructor_ready(client, ops, email="c-inst-b@catchap.dev")
    ops_course = _create_course(client, otok, title="다른 강사 코스", subject="과학")
    my_course = _create_course(client, itok, title="강사 코스", subject="영어")

    # 목록: 강사=자기 것만, 운영자=전체
    assert [x["id"] for x in client.get("/api/v1/ops/courses", headers=auth(itok)).json()] == [my_course["id"]]
    assert {x["id"] for x in client.get("/api/v1/ops/courses", headers=auth(ops)).json()} >= {
        ops_course["id"], my_course["id"]
    }

    # 남의 코스 수정·삭제 404
    oid = ops_course["id"]
    assert client.put(f"/api/v1/ops/courses/{oid}", json={"title": "탈취"}, headers=auth(itok)).status_code == 404
    assert client.delete(f"/api/v1/ops/courses/{oid}", headers=auth(itok)).status_code == 404


def test_lecture_course_link_and_subject_match(client, db, media_dir):
    """강의를 코스에 담을 때 소유·과목 일치 강제. 코스 삭제 시 강의는 미분류로 보존."""
    ops = _instructor(client, db)
    course = _create_course(client, ops, title="과학 코스", subject="과학")

    # 과목 불일치 — 국어 강의를 과학 코스에 담으려 하면 400
    bad = _upload_lecture(client, ops, title="국어강의", subject="국어", course_id=course["id"])
    assert bad.status_code == 400 and "과목" in bad.json()["detail"]

    # 과목 일치 — 성공, course_id 귀속
    ok = _upload_lecture(client, ops, title="과학강의", subject="과학", course_id=course["id"])
    assert ok.status_code == 200, ok.text
    assert ok.json()["course_id"] == course["id"]

    # 코스에 강의 수 반영
    courses = client.get("/api/v1/ops/courses", headers=auth(ops)).json()
    assert next(x for x in courses if x["id"] == course["id"])["lecture_count"] == 1

    # 코스 삭제 → 강의는 미분류(course_id=None)로 살아남는다
    d = client.delete(f"/api/v1/ops/courses/{course['id']}", headers=auth(ops))
    assert d.status_code == 200 and d.json()["lectures_unassigned"] == 1
    from app.models import Lecture

    assert db.get(Lecture, ok.json()["id"]).course_id is None
    assert db.get(Lecture, ok.json()["id"]).status != "deleted"  # 강의 자체는 보존


def test_update_lecture_course_reassign(client, db, media_dir):
    """강의 수정으로 코스 이동/해제 — 명시 전송만 반영, 과목 일치 강제."""
    ops = _instructor(client, db)
    c1 = _create_course(client, ops, title="수학A", subject="수학")
    c2 = _create_course(client, ops, title="영어A", subject="영어")
    lec = _upload_lecture(client, ops, title="수학강의", subject="수학", course_id=c1["id"]).json()

    # 과목 다른 코스로 이동 400
    assert client.put(f"/api/v1/ops/lectures/{lec['id']}", json={"course_id": c2["id"]}, headers=auth(ops)).status_code == 400

    # null 전송 = 미분류로 빼기
    r = client.put(f"/api/v1/ops/lectures/{lec['id']}", json={"course_id": None}, headers=auth(ops))
    assert r.status_code == 200 and r.json()["course_id"] is None

    # 미전송 = 유지(다른 필드만 수정)
    client.put(f"/api/v1/ops/lectures/{lec['id']}", json={"course_id": c1["id"]}, headers=auth(ops))
    r = client.put(f"/api/v1/ops/lectures/{lec['id']}", json={"title": "제목만"}, headers=auth(ops))
    assert r.json()["course_id"] == c1["id"]  # 코스 유지


def test_lecture_reorder_within_course(client, db, media_dir):
    """드래그 재배열 — 넘어온 차례대로 order_no=1,2,3, 목록 순서도 그대로 바뀐다."""
    from app.models import Lecture

    ops = _instructor(client, db)
    course = _create_course(client, ops, title="영어 코스", subject="영어")
    a = _upload_lecture(client, ops, title="A강", subject="영어", course_id=course["id"]).json()
    b = _upload_lecture(client, ops, title="B강", subject="영어", course_id=course["id"]).json()
    c = _upload_lecture(client, ops, title="C강", subject="영어", course_id=course["id"]).json()

    # 업로드 순서 A,B,C(order_no 1,2,3)를 역순 C,B,A로 재배열
    r = client.put(
        "/api/v1/ops/lectures/reorder",
        json={"lecture_ids": [c["id"], b["id"], a["id"]]},
        headers=auth(ops),
    )
    assert r.status_code == 200 and r.json()["count"] == 3
    assert db.get(Lecture, c["id"]).order_no == 1
    assert db.get(Lecture, b["id"]).order_no == 2
    assert db.get(Lecture, a["id"]).order_no == 3

    # 목록 순서(과목·order_no·created_at)도 C,B,A
    listed = [
        x["title"]
        for x in client.get("/api/v1/ops/lectures", headers=auth(ops)).json()
        if x["course_id"] == course["id"]
    ]
    assert listed == ["C강", "B강", "A강"]

    # 중복 강의가 섞이면 400(부분 재부여로 뒤섞이는 것 방지)
    assert (
        client.put(
            "/api/v1/ops/lectures/reorder",
            json={"lecture_ids": [a["id"], a["id"]]},
            headers=auth(ops),
        ).status_code
        == 400
    )


def test_lecture_reorder_scope_foreign_404(client, db, media_dir):
    """강사는 남의 강의를 섞어 재배열할 수 없다(404, 존재 미노출). 자기 것만은 OK."""
    ops = _ops(client, db)
    created, itok = _instructor_ready(client, ops, email="reord-inst@catchap.dev")
    # '남의 강의'는 다른 강사 것 — 운영자는 저작하지 않으므로(감독·검수만, 0720).
    other, otok = _instructor_ready(client, ops, email="reord-inst-b@catchap.dev")
    ops_lec = _upload_lecture(client, otok, title="다른 강사 강의", subject="수학").json()
    my_lec = _upload_lecture(client, itok, title="강사강의", subject="수학").json()

    # 자기 것 + 남의 것을 섞어 보내면 404 — 그리고 order_no는 전혀 바뀌지 않는다(원자적)
    from app.models import Lecture

    before = db.get(Lecture, my_lec["id"]).order_no
    assert (
        client.put(
            "/api/v1/ops/lectures/reorder",
            json={"lecture_ids": [my_lec["id"], ops_lec["id"]]},
            headers=auth(itok),
        ).status_code
        == 404
    )
    db.expire_all()
    assert db.get(Lecture, my_lec["id"]).order_no == before  # 부분 변경 없음

    # 강사 자기 강의만은 재배열 성공
    assert (
        client.put(
            "/api/v1/ops/lectures/reorder",
            json={"lecture_ids": [my_lec["id"]]},
            headers=auth(itok),
        ).status_code
        == 200
    )


def test_student_sees_courses_and_lecture_course_id(client, db, seed_org, media_dir):
    """3단계 학생 화면 — 학생이 활성 코스 목록(강사명·강의수)과 강의별 course_id를 본다.
    빈 코스(활성 강의 0)는 학생 목록에서 빠지고, 상세 toc는 코스 스코프(미분류는 과목 스코프)."""
    ops = _instructor(client, db)
    course = _create_course(client, ops, title="수학 코스", subject="수학")
    empty = _create_course(client, ops, title="빈 코스", subject="수학")  # 강의 없음 → 학생 목록 제외
    a = _upload_lecture(client, ops, title="1강", subject="수학", course_id=course["id"]).json()
    b = _upload_lecture(client, ops, title="2강", subject="수학", course_id=course["id"]).json()
    solo = _upload_lecture(client, ops, title="미분류강의", subject="수학").json()  # course_id 없음

    stok = _student_token(client, seed_org)

    # 학생 코스 목록 — 활성 강의 있는 코스만, 강사명·강의수 포함
    cs = client.get("/api/v1/courses", headers=auth(stok)).json()
    ids = [c["id"] for c in cs]
    assert course["id"] in ids and empty["id"] not in ids  # 빈 코스는 학생에게 안 보임
    mine = next(c for c in cs if c["id"] == course["id"])
    assert mine["lecture_count"] == 2 and mine["instructor_name"]  # 강사명 해석됨

    # 강의 목록 — 각 강의에 course_id(미분류는 None)
    lects = {x["id"]: x for x in client.get("/api/v1/lectures", headers=auth(stok)).json()}
    assert lects[a["id"]]["course_id"] == course["id"]
    assert lects[solo["id"]]["course_id"] is None

    # 코스 강의 상세 — 수강신청 게이트가 생겨(0722) 신청해야 상세·시청 가능. 목록·카탈로그는
    # 게이트 밖(둘러보기 유지)이라 위 assert들은 신청 전에도 통과한다. 상세부터 신청 필요.
    assert client.post(f"/api/v1/courses/{course['id']}/enroll", headers=auth(stok)).status_code == 200
    # 코스 강의 상세 — course_id + toc는 코스 스코프(같은 코스 2강, 미분류 solo 제외)
    detail = client.get(f"/api/v1/lectures/{a['id']}", headers=auth(stok)).json()
    assert detail["course_id"] == course["id"]
    assert {t["id"] for t in detail["toc"]} == {a["id"], b["id"]}

    # 미분류 강의 상세 — toc는 같은 과목의 미분류만(코스 강의는 안 섞인다)
    solo_detail = client.get(f"/api/v1/lectures/{solo['id']}", headers=auth(stok)).json()
    assert solo_detail["course_id"] is None
    toc_ids = {t["id"] for t in solo_detail["toc"]}
    assert solo["id"] in toc_ids and a["id"] not in toc_ids


def test_course_enroll_unenroll_and_flag(client, db, seed_org, media_dir):
    """수강신청 → 목록 enrolled=true → 취소(withdrawn) → false → 재신청(같은 행 되살림·1행
    유지·진도 이어감). 없는 코스는 404. 무료 자유 신청·취소(Coursera 무료 모델)."""
    from app.models import CourseEnrollment

    ops = _instructor(client, db)
    course = _create_course(client, ops, title="영어 코스", subject="영어")
    _upload_lecture(client, ops, title="1강", subject="영어", course_id=course["id"])
    cid = course["id"]
    stok = _student_token(client, seed_org)

    def enrolled():
        cs = client.get("/api/v1/courses", headers=auth(stok)).json()
        return next((c["enrolled"] for c in cs if c["id"] == cid), None)

    # 신청 전 — 목록에 코스는 보이되 enrolled=false(둘러보기 가능, 오픈 카탈로그)
    assert enrolled() is False

    # 수강신청 → enrolled=true, DB active
    r = client.post(f"/api/v1/courses/{cid}/enroll", headers=auth(stok))
    assert r.status_code == 200 and r.json()["enrolled"] is True
    assert enrolled() is True
    assert db.query(CourseEnrollment).filter_by(course_id=cid).first().status == "active"

    # 수강취소 → enrolled=false, DB withdrawn(행은 남아 진도 보존)
    r = client.delete(f"/api/v1/courses/{cid}/enroll", headers=auth(stok))
    assert r.status_code == 200 and r.json()["enrolled"] is False
    assert enrolled() is False
    db.expire_all()
    assert db.query(CourseEnrollment).filter_by(course_id=cid).first().status == "withdrawn"

    # 재신청 → 같은 행을 active로 되살림(1행 유지)
    assert client.post(f"/api/v1/courses/{cid}/enroll", headers=auth(stok)).json()["enrolled"] is True
    db.expire_all()
    assert db.query(CourseEnrollment).filter_by(course_id=cid).count() == 1
    assert db.query(CourseEnrollment).filter_by(course_id=cid).first().status == "active"

    # 없는 코스는 404
    bad = client.post("/api/v1/courses/00000000-0000-0000-0000-000000000000/enroll", headers=auth(stok))
    assert bad.status_code == 404


def test_edit_lecture_into_general_subject_course(client, db, seed_org, media_dir):
    """강의 수정으로 '일반' 과목 코스(인라인 생성 코스 등)에 배정 가능.
    (회귀: PUT 검증이 '일반'을 '지원하지 않는 과목'으로 막아, 강의 수정→코스 배정이 실패했다.)"""
    ops = _instructor(client, db)
    course = _create_course(client, ops, title="안전", subject="일반")  # 코스 중심 기본 과목
    lec = _upload_lecture(client, ops, title="응급처치", subject="국어").json()

    # 강의 수정으로 '일반' 코스 배정 — subject도 코스 따라 '일반'(프론트 동작). 이제 200(전엔 400).
    r = client.put(
        f"/api/v1/ops/lectures/{lec['id']}",
        json={"course_id": course["id"], "subject": "일반"},
        headers=auth(ops),
    )
    assert r.status_code == 200, r.text
    assert r.json()["course_id"] == course["id"] and r.json()["subject"] == "일반"


def test_enrollment_gates_lecture_watch(client, db, seed_org, media_dir):
    """수강신청 게이트 — 코스 강의는 신청해야 시청·재생 가능(미신청 403·reason=not_enrolled),
    신청하면 200, 취소하면 다시 403. 미분류(course 없는) 강의는 신청 없이도 열린다."""
    ops = _instructor(client, db)
    course = _create_course(client, ops, title="게이트 코스", subject="수학")
    lec = _upload_lecture(client, ops, title="1강", subject="수학", course_id=course["id"]).json()
    free = _upload_lecture(client, ops, title="미분류강의", subject="수학").json()  # course_id 없음
    stok = _student_token(client, seed_org)

    # 미신청 — 코스 강의 상세·재생 모두 403(not_enrolled)
    d = client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(stok))
    assert d.status_code == 403
    assert d.json()["detail"]["reason"] == "not_enrolled"
    assert client.post(f"/api/v1/lectures/{lec['id']}/session", headers=auth(stok)).status_code == 403

    # 미분류 강의는 신청 없이도 열린다(신청할 코스가 없으므로)
    assert client.get(f"/api/v1/lectures/{free['id']}", headers=auth(stok)).status_code == 200

    # 수강신청 후 — 코스 강의 상세·재생 200
    client.post(f"/api/v1/courses/{course['id']}/enroll", headers=auth(stok))
    assert client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(stok)).status_code == 200
    assert client.post(f"/api/v1/lectures/{lec['id']}/session", headers=auth(stok)).status_code == 200

    # 수강취소하면 다시 막힌다
    client.delete(f"/api/v1/courses/{course['id']}/enroll", headers=auth(stok))
    assert client.get(f"/api/v1/lectures/{lec['id']}", headers=auth(stok)).status_code == 403
