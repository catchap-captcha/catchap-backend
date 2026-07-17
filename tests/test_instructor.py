"""강사(instructor) 역할 — 초대 발급·로그인·강의 소유권 스코프(운영자=전체/강사=자기 것)."""

from tests.test_captcha_api import _ops, auth
from tests.test_lectures import _upload_lecture, media_dir  # noqa: F401 (fixture 재사용)


def _create_instructor(client, ops_tok, *, name="김강사", email="inst1@catchap.dev"):
    r = client.post(
        "/api/v1/ops/instructors", json={"name": name, "email": email}, headers=auth(ops_tok)
    )
    assert r.status_code == 200, r.text
    return r.json()


def _instructor_login(client, email, password):
    return client.post("/api/v1/auth/ops-login", json={"email": email, "password": password})


def test_instructor_invite_login_and_role_walls(client, db, media_dir):
    """초대 발급 → 임시비번 로그인 → 강의 콘솔 접근 OK, 운영 메뉴는 403, 일반 로그인은 불가."""
    ops_tok = _ops(client, db)
    created = _create_instructor(client, ops_tok)
    assert created["temp_password"]  # 이메일 dry-run 대비 1회 노출

    # 같은 숨겨진 진입구(/auth/ops-login)로 로그인
    r = _instructor_login(client, "inst1@catchap.dev", created["temp_password"])
    assert r.status_code == 200, r.text
    inst_tok = r.json()["access_token"]

    # 강의 제작 도메인 접근 가능
    assert client.get("/api/v1/ops/lectures", headers=auth(inst_tok)).status_code == 200
    # 운영 전용 메뉴는 전부 벽 — 설정(AI 키)·기관·운영자·강사 관리
    for path in ("/api/v1/ops/settings/ai", "/api/v1/ops/operators", "/api/v1/ops/instructors"):
        assert client.get(path, headers=auth(inst_tok)).status_code == 403, path

    # 일반 로그인 폼으로는 인증 불가(운영자와 동일 규약 — 존재 여부 미노출)
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "inst1@catchap.dev", "password": created["temp_password"]},
    )
    assert r.status_code in (400, 401, 403)


def test_instructor_scope_own_lectures_only(client, db, media_dir):
    """강사는 자기 강의만 — 목록 필터 + 남의 강의는 모든 제작 경로에서 404(존재 미노출)."""
    ops_tok = _ops(client, db)
    created = _create_instructor(client, ops_tok, email="inst2@catchap.dev")
    inst_tok = _instructor_login(
        client, "inst2@catchap.dev", created["temp_password"]
    ).json()["access_token"]

    ops_lec = _upload_lecture(client, ops_tok, title="운영자 강의").json()
    my_lec = _upload_lecture(client, inst_tok, title="강사 강의", subject="영어").json()

    # 목록: 강사=자기 것만, 운영자=전체
    mine = client.get("/api/v1/ops/lectures", headers=auth(inst_tok)).json()
    assert [l["id"] for l in mine] == [my_lec["id"]]
    all_rows = client.get("/api/v1/ops/lectures", headers=auth(ops_tok)).json()
    assert {l["id"] for l in all_rows} == {ops_lec["id"], my_lec["id"]}

    # 남의 강의 — 수정·삭제·문항 목록·문항 생성·자료 목록·미리보기 전부 404
    oid = ops_lec["id"]
    assert (
        client.put(f"/api/v1/ops/lectures/{oid}", json={"title": "탈취"}, headers=auth(inst_tok)).status_code
        == 404
    )
    assert client.delete(f"/api/v1/ops/lectures/{oid}", headers=auth(inst_tok)).status_code == 404
    assert (
        client.get(f"/api/v1/ops/lectures/{oid}/questions", headers=auth(inst_tok)).status_code == 404
    )
    assert (
        client.post(
            f"/api/v1/ops/lectures/{oid}/questions",
            json={"position_sec": 1, "prompt": "x", "options": ["a", "b"], "answer_index": 0},
            headers=auth(inst_tok),
        ).status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/ops/lectures/{oid}/materials", headers=auth(inst_tok)).status_code == 404
    )
    assert (
        client.post(f"/api/v1/ops/lectures/{oid}/preview", headers=auth(inst_tok)).status_code == 404
    )

    # 자기 강의는 전부 가능 — 수정·문항 등록, uploaded_by 귀속 확인
    r = client.put(
        f"/api/v1/ops/lectures/{my_lec['id']}", json={"title": "강사 강의(수정)"}, headers=auth(inst_tok)
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/api/v1/ops/lectures/{my_lec['id']}/questions",
        json={"position_sec": 1, "prompt": "내 문항", "options": ["가", "나"], "answer_index": 0},
        headers=auth(inst_tok),
    )
    assert r.status_code == 200, r.text
    from app.models import Lecture

    assert db.get(Lecture, my_lec["id"]).uploaded_by == created["id"]

    # 운영자는 강사 강의도 감독 가능(전체 스코프)
    assert (
        client.put(
            f"/api/v1/ops/lectures/{my_lec['id']}", json={"title": "운영자 개입"}, headers=auth(ops_tok)
        ).status_code
        == 200
    )


def test_disabled_instructor_cannot_login(client, db):
    """중지된 강사는 로그인 403 + 기존 세션 폐기, 재개하면 다시 로그인 가능."""
    ops_tok = _ops(client, db)
    created = _create_instructor(client, ops_tok, email="inst3@catchap.dev")

    r = client.patch(
        f"/api/v1/ops/instructors/{created['id']}", json={"status": "disabled"}, headers=auth(ops_tok)
    )
    assert r.status_code == 200 and r.json()["status"] == "disabled"
    r = _instructor_login(client, "inst3@catchap.dev", created["temp_password"])
    assert r.status_code == 403

    r = client.patch(
        f"/api/v1/ops/instructors/{created['id']}", json={"status": "active"}, headers=auth(ops_tok)
    )
    assert r.status_code == 200
    assert _instructor_login(client, "inst3@catchap.dev", created["temp_password"]).status_code == 200
