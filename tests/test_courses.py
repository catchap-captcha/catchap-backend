"""강사 코스(1단계) — CRUD·과목 고정·강사 소유 스코프·강의 연결·기존 강의 호환.

설계: docs/product-direction.md(코스=과목 고정), 강사 스코프는 test_instructor와 동일 규약.
"""

from tests.test_captcha_api import _ops, auth
from tests.test_instructor import _create_instructor, _instructor_login
from tests.test_lectures import _upload_lecture, media_dir  # noqa: F401 (fixture 재사용)


def _create_course(client, tok, *, title="수학 기초반", subject="수학"):
    r = client.post("/api/v1/ops/courses", json={"title": title, "subject": subject}, headers=auth(tok))
    assert r.status_code == 200, r.text
    return r.json()


def test_course_crud_and_subject_fixed(client, db, media_dir):
    ops = _ops(client, db)
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
    created = _create_instructor(client, ops, email="c-inst@catchap.dev")
    itok = _instructor_login(client, "c-inst@catchap.dev", created["temp_password"]).json()["access_token"]

    ops_course = _create_course(client, ops, title="운영자 코스", subject="과학")
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
    ops = _ops(client, db)
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
    ops = _ops(client, db)
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

    ops = _ops(client, db)
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
    created = _create_instructor(client, ops, email="reord-inst@catchap.dev")
    itok = _instructor_login(client, "reord-inst@catchap.dev", created["temp_password"]).json()["access_token"]
    ops_lec = _upload_lecture(client, ops, title="운영자강의", subject="수학").json()
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
