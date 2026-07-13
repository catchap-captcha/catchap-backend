"""운영자 임시비번 재설정 로그인 + 교사 '관심 필요 학생' 정직성 회귀 테스트."""

from datetime import datetime

from app.core.security import hash_password


def auth(t):
    return {"Authorization": f"Bearer {t}"}


def _ops(client, db, email="resetops@t.dev"):
    from app.models import User

    ops = User(
        email=email, password_hash=hash_password("Password123!"), name="운영자",
        role="ops", email_verified_at=datetime.utcnow(),
    )
    db.add(ops)
    db.commit()
    r = client.post("/api/v1/auth/ops-login", json={"email": email, "password": "Password123!"})
    return ops, r.json()["access_token"]


def test_ops_reset_password_login_flow(client, db):
    """재설정 임시비번으로 로그인 성공 + 붙여넣기 공백 패딩 허용(메일 복사 사고)."""
    _, tok = _ops(client, db)
    r = client.post("/api/v1/ops/operators", json={"name": "김운영", "email": "newop@t.dev"},
                    headers=auth(tok))
    assert r.status_code == 200, r.text
    op_id = r.json()["id"]

    r2 = client.post(f"/api/v1/ops/operators/{op_id}/reset-password", headers=auth(tok))
    assert r2.status_code == 200, r2.text
    pw = r2.json()["temp_password"]

    # 정확한 임시비번 → 성공
    ok = client.post("/api/v1/auth/ops-login", json={"email": "newop@t.dev", "password": pw})
    assert ok.status_code == 200, ok.text
    # 메일에서 복사하며 공백/개행이 붙어도 성공 (실사용 복붙 사고 관용)
    ok2 = client.post("/api/v1/auth/ops-login", json={"email": "newop@t.dev", "password": f" {pw}\n"})
    assert ok2.status_code == 200, ok2.text
    # 전혀 다른 비번은 여전히 거부
    bad = client.post("/api/v1/auth/ops-login", json={"email": "newop@t.dev", "password": pw + "x"})
    assert bad.status_code == 401


def test_ops_reset_password_disabled_account_rejected(client, db):
    """중지 계정 재설정은 409 — 메일만 가고 로그인은 403이라 '재설정했는데 안 됨'이 되는 함정 차단."""
    from app.models import User

    _, tok = _ops(client, db, email="resetops2@t.dev")
    dis = User(
        email="disabled@t.dev", password_hash=hash_password("Password123!"), name="중지운영자",
        role="ops", status="disabled", email_verified_at=datetime.utcnow(),
    )
    db.add(dis)
    db.commit()

    r = client.post(f"/api/v1/ops/operators/{dis.id}/reset-password", headers=auth(tok))
    assert r.status_code == 409
    assert "중지된 계정" in r.json()["detail"]


def test_login_role_tab_enforced(client, db, seed_org):
    """로그인 탭 역할 강제 — 학부모 탭에서 교사 계정이 교사로 로그인되던 혼선 차단."""
    from app.models import User

    parent = User(
        email="roleparent@t.dev", password_hash=hash_password("Password123!"),
        name="역할학부모", role="parent", email_verified_at=datetime.utcnow(),
    )
    db.add(parent)
    db.commit()

    def try_login(role, email):
        body = {"email": email, "password": "Password123!"}
        if role is not None:
            body["role"] = role
        return client.post("/api/v1/auth/login", json=body)

    # 학부모 탭 + 교사 계정 → 403 (계정 종류 안내)
    r = try_login("parent", "t1@test.dev")
    assert r.status_code == 403, r.text
    assert "선생님 계정" in r.json()["detail"]
    # 기관 탭 + 학부모 계정 → 403
    r2 = try_login("org", "roleparent@t.dev")
    assert r2.status_code == 403
    assert "학부모 계정" in r2.json()["detail"]
    # 올바른 조합은 통과: 기관 탭 그룹(교사 포함)·학부모 탭·정확 역할·미지정(하위호환)
    assert try_login("org", "t1@test.dev").status_code == 200
    assert try_login("parent", "roleparent@t.dev").status_code == 200
    assert try_login("teacher", "t1@test.dev").status_code == 200
    assert try_login(None, "t1@test.dev").status_code == 200


def test_teacher_attention_empty_stays_empty(client, db, seed_org):
    """반이 건강(관심 필요 0명)하면 attention은 빈 목록 — 데모 아동 이름 복귀 금지."""
    from app.models import LearningAttempt

    org, cls, teacher, student = (
        seed_org["org"], seed_org["class"], seed_org["teacher"], seed_org["student"],
    )
    # 전부 정답(정답률 100% ≥ 70) — 도움 필요 기준 미달
    for _ in range(6):
        db.add(
            LearningAttempt(
                organization_id=org.id, student_id=student.id, subject="수학",
                chapter_no=1, result="correct", score=100,
            )
        )
    db.commit()

    r = client.post(
        "/api/v1/auth/login",
        json={"role": "teacher", "email": "t1@test.dev", "password": "Password123!"},
    )
    tok = r.json()["access_token"]
    d = client.get("/api/v1/teacher/dashboard", headers=auth(tok)).json()
    assert d["demo"] is False, "실 시도가 있으므로 demo가 아니어야 한다"
    assert d["attention"] == [], f"관심 필요 0명인데 데모 이름이 살아났다: {d['attention']}"

    a = client.get("/api/v1/teacher/analytics", headers=auth(tok)).json()
    assert a["attention"] == [], f"분석 화면도 동일해야 한다: {a['attention']}"
