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


def _change_password(client, tok, new_password="NewPass123!"):
    """임시비번 강제변경 게이트 통과 — 새 비번 설정(must_change_password 해제). 토큰은 그대로 유효."""
    r = client.post(
        "/api/v1/settings/me/change-password",
        json={"new_password": new_password},
        headers=auth(tok),
    )
    assert r.status_code == 200, r.text
    return tok


def _instructor_ready(client, ops_tok, *, email="inst1@catchap.dev", name="김강사"):
    """초대 발급 → 임시비번 로그인 → 강제 변경까지 마친, 바로 쓸 수 있는 강사 (created, token).

    강제변경 게이트(must_change_password면 콘솔 차단) 때문에, 콘텐츠 저작 테스트는 로그인 직후
    비번을 바꿔 게이트를 통과한 토큰이 필요하다."""
    created = _create_instructor(client, ops_tok, name=name, email=email)
    tok = _instructor_login(client, email, created["temp_password"]).json()["access_token"]
    _change_password(client, tok)
    return created, tok


def test_instructor_invite_login_and_role_walls(client, db, media_dir):
    """초대 발급 → 임시비번 로그인 → (게이트) 변경 전 콘솔 차단, 변경 후 접근 OK, 운영 메뉴는 403."""
    ops_tok = _ops(client, db)
    created = _create_instructor(client, ops_tok)
    assert created["temp_password"]  # 이메일 dry-run 대비 1회 노출

    # 일반 로그인 폼으로는 인증 불가(운영자와 동일 규약 — 존재 여부 미노출). 임시비번 유효 시점에 확인.
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "inst1@catchap.dev", "password": created["temp_password"]},
    )
    assert r.status_code in (400, 401, 403)

    # 같은 숨겨진 진입구(/auth/ops-login)로 로그인
    r = _instructor_login(client, "inst1@catchap.dev", created["temp_password"])
    assert r.status_code == 200, r.text
    inst_tok = r.json()["access_token"]

    # ★강제변경 게이트: 임시비번(must_change_password) 상태에선 허용목록 외 전부 403
    assert client.get("/api/v1/ops/lectures", headers=auth(inst_tok)).status_code == 403
    # /auth/me 는 허용 — ForcePasswordGate 화면이 이 플래그를 읽어야 뜬다
    me = client.get("/api/v1/auth/me", headers=auth(inst_tok))
    assert me.status_code == 200 and me.json()["must_change_password"] is True

    # 비번 변경(허용목록 경로) → 게이트 해제 → 강의 콘솔 접근 가능
    _change_password(client, inst_tok)
    assert client.get("/api/v1/ops/lectures", headers=auth(inst_tok)).status_code == 200

    # 운영 전용 메뉴는 (게이트 해제 후에도) 역할 벽으로 403 — 설정(AI 키)·기관·운영자·강사 관리
    for path in ("/api/v1/ops/settings/ai", "/api/v1/ops/operators", "/api/v1/ops/instructors"):
        assert client.get(path, headers=auth(inst_tok)).status_code == 403, path


def test_instructor_scope_own_lectures_only(client, db, media_dir):
    """강사는 자기 강의만 — 목록 필터 + 남의 강의는 모든 제작 경로에서 404(존재 미노출)."""
    ops_tok = _ops(client, db)
    created, inst_tok = _instructor_ready(client, ops_tok, email="inst2@catchap.dev")

    # '남의 강의'는 다른 강사가 올린다 — 운영자는 저작(업로드)을 하지 않으므로(감독·검수만, 0720).
    other, other_tok = _instructor_ready(client, ops_tok, email="inst2b@catchap.dev")
    ops_lec = _upload_lecture(client, other_tok, title="다른 강사 강의").json()
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

    # 운영자는 감독·검수만(0720) — 강사 강의를 공개/숨김(status)은 할 수 있지만(모더레이션),
    # 내용(제목 등)은 편집할 수 없다(403). 저작은 강사 전용.
    assert (
        client.put(
            f"/api/v1/ops/lectures/{my_lec['id']}", json={"status": "hidden"}, headers=auth(ops_tok)
        ).status_code
        == 200
    )
    assert (
        client.put(
            f"/api/v1/ops/lectures/{my_lec['id']}", json={"title": "운영자 개입"}, headers=auth(ops_tok)
        ).status_code
        == 403
    )


def test_ops_is_review_only_not_author(client, db, media_dir):
    """★ops 권한 B(0720) — 운영자는 감독·검수만: 콘텐츠 저작(생성·업로드·문항)은 403,
    조회는 가능(감독), 공개/숨김(status)은 가능하지만 내용 편집은 403(모더레이션만)."""
    ops_tok = _ops(client, db)
    created, itok = _instructor_ready(client, ops_tok, email="author-inst@catchap.dev")

    # 강사가 저작(강의·코스·문항)
    lec = _upload_lecture(client, itok, title="강사 강의").json()
    course = client.post(
        "/api/v1/ops/courses", json={"title": "강사 코스", "subject": "수학"}, headers=auth(itok)
    ).json()

    # 운영자 저작 시도 → 전부 403 (require_content_author = instructor 전용)
    assert client.post(
        "/api/v1/ops/courses", json={"title": "운영자 코스", "subject": "수학"}, headers=auth(ops_tok)
    ).status_code == 403
    assert _upload_lecture(client, ops_tok, title="운영자 강의").status_code == 403
    assert client.post(
        f"/api/v1/ops/lectures/{lec['id']}/questions",
        json={"position_sec": 1, "prompt": "x", "options": ["a", "b"], "answer_index": 0},
        headers=auth(ops_tok),
    ).status_code == 403

    # 운영자 조회는 가능(감독)
    assert client.get("/api/v1/ops/lectures", headers=auth(ops_tok)).status_code == 200
    assert client.get(f"/api/v1/ops/lectures/{lec['id']}/questions", headers=auth(ops_tok)).status_code == 200

    # 운영자 모더레이션: 공개/숨김은 OK, 내용 편집은 403
    assert client.put(
        f"/api/v1/ops/lectures/{lec['id']}", json={"status": "hidden"}, headers=auth(ops_tok)
    ).status_code == 200
    assert client.put(
        f"/api/v1/ops/lectures/{lec['id']}", json={"title": "편집"}, headers=auth(ops_tok)
    ).status_code == 403
    assert client.put(
        f"/api/v1/ops/courses/{course['id']}", json={"status": "hidden"}, headers=auth(ops_tok)
    ).status_code == 200
    assert client.put(
        f"/api/v1/ops/courses/{course['id']}", json={"title": "편집"}, headers=auth(ops_tok)
    ).status_code == 403


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


def test_instructor_temp_password_expires(client, db):
    """임시 비번 72h 만료 — 발급 시 만료 시각이 설정되고, 만료 후엔 임시비번 로그인이 403(재발급
    필요). 운영자가 재설정하면 새 임시비번 + 새 만료로 다시 로그인 가능."""
    from datetime import datetime, timedelta

    from app.models import User

    ops_tok = _ops(client, db)
    created = _create_instructor(client, ops_tok, email="exp-inst@catchap.dev")

    # 발급 시 만료 시각이 미래(≈72h 뒤)로 설정된다
    inst = db.query(User).filter(User.email == "exp-inst@catchap.dev").first()
    assert inst.password_reset_expires_at is not None
    assert inst.password_reset_expires_at > datetime.now()

    # 만료 시각을 과거로 당기면 임시비번 로그인 자체가 막힌다(403)
    inst.password_reset_expires_at = datetime.now() - timedelta(minutes=1)
    db.commit()
    assert (
        _instructor_login(client, "exp-inst@catchap.dev", created["temp_password"]).status_code == 403
    )

    # 운영자가 재설정 → 새 임시비번 + 새 만료 → 다시 로그인 가능(이후 강제 변경 게이트는 별개)
    res = client.post(
        f"/api/v1/ops/instructors/{created['id']}/reset-password", headers=auth(ops_tok)
    ).json()
    assert (
        _instructor_login(client, "exp-inst@catchap.dev", res["temp_password"]).status_code == 200
    )
