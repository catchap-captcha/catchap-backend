"""보조 담임(assistant_teacher) — 담임 결원 대체 배정 검증."""

from datetime import datetime

from app.core.security import hash_password
from app.models import ClassRoom, Membership, User


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def _org_admin(db, org):
    admin = User(
        email="principal@test.dev", password_hash=hash_password("Password123!"),
        name="교장", role="org_admin", organization_id=org.id,
        email_verified_at=datetime.utcnow(),
    )
    db.add(admin)
    db.commit()
    return admin


def _login(client, email):
    return client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"}).json()["access_token"]


def test_assign_assistant_teacher(client, db, seed_org):
    org = seed_org["org"]
    _org_admin(db, org)
    admin = _login(client, "principal@test.dev")

    # 보조 교사 계정 + 멤버십 생성
    assistant = User(
        email="assist@test.dev", password_hash=hash_password("Password123!"),
        name="보조샘", role="teacher", organization_id=org.id,
        email_verified_at=datetime.utcnow(),
    )
    db.add(assistant)
    db.flush()
    m = Membership(
        user_id=assistant.id, organization_id=org.id, role="teacher",
        status="active", teacher_code="T-9001", position="보조",
    )
    db.add(m)
    db.commit()

    # 보조 교사를 1-1반(seed class)의 보조 담임으로 연결
    r = client.patch(
        f"/api/v1/orgs/{org.id}/teachers/{m.id}",
        json={"role": "보조", "class_name": "1-1반"},
        headers=auth(admin),
    )
    assert r.status_code == 200, r.text

    db.expire_all()
    cls = db.get(ClassRoom, seed_org["class"].id)
    assert cls.assistant_teacher_id == assistant.id  # 보조 담임으로 연결됨
    assert cls.teacher_id == seed_org["teacher"].id  # 기존 담임은 그대로

    # 보조 교사로 로그인 → 담임 결원 대체로 그 반을 볼 수 있어야 함
    atok = _login(client, "assist@test.dev")
    dash = client.get("/api/v1/teacher/class/students", headers=auth(atok))
    assert dash.status_code == 200, dash.text
    assert dash.json()["class_id"] == cls.id  # 보조도 담당 학급으로 인식


def test_deleted_teacher_is_deprovisioned(client, db, seed_org):
    """삭제된 교사는 계정 비활성 + 토큰 폐기로 즉시 접근 차단(디프로비저닝)."""
    org = seed_org["org"]
    _org_admin(db, org)
    admin = _login(client, "principal@test.dev")

    u = User(
        email="temp@test.dev", password_hash=hash_password("Password123!"),
        name="임시샘", role="teacher", organization_id=org.id,
        email_verified_at=datetime.utcnow(),
    )
    db.add(u)
    db.flush()
    m = Membership(user_id=u.id, organization_id=org.id, role="teacher", status="active", teacher_code="T-7777")
    db.add(m)
    db.commit()

    tok = _login(client, "temp@test.dev")
    assert client.get("/api/v1/auth/me", headers=auth(tok)).status_code == 200  # 삭제 전 접근 가능

    assert client.request("DELETE", f"/api/v1/orgs/{org.id}/teachers/{m.id}", headers=auth(admin)).status_code == 200
    db.expire_all()
    # 삭제 후 기존 토큰으로도 접근 거부 (User.status=disabled)
    assert client.get("/api/v1/auth/me", headers=auth(tok)).status_code == 401


def test_dissolve_clears_assistant_link(client, db, seed_org):
    org = seed_org["org"]
    _org_admin(db, org)
    admin = _login(client, "principal@test.dev")
    cid = client.post(f"/api/v1/orgs/{org.id}/classes", json={"name": "6-6반"}, headers=auth(admin)).json()["class"]["id"]

    u = User(
        email="a3@test.dev", password_hash=hash_password("Password123!"),
        name="보조셋", role="teacher", organization_id=org.id, email_verified_at=datetime.utcnow(),
    )
    db.add(u)
    db.flush()
    m = Membership(user_id=u.id, organization_id=org.id, role="teacher", status="active", teacher_code="T-9003", position="보조")
    db.add(m)
    db.commit()
    client.patch(f"/api/v1/orgs/{org.id}/teachers/{m.id}", json={"role": "보조", "class_name": "6-6반"}, headers=auth(admin))

    db.expire_all()
    assert db.get(ClassRoom, cid).assistant_teacher_id == u.id
    # 해체하면 보조 연결도 풀린다
    assert client.request("DELETE", f"/api/v1/orgs/{org.id}/classes/{cid}", headers=auth(admin)).status_code == 200
    db.expire_all()
    assert db.get(ClassRoom, cid).assistant_teacher_id is None


def test_classes_list_shows_assistant(client, db, seed_org):
    org = seed_org["org"]
    _org_admin(db, org)
    admin = _login(client, "principal@test.dev")
    assistant = User(
        email="a2@test.dev", password_hash=hash_password("Password123!"),
        name="보조둘", role="teacher", organization_id=org.id,
        email_verified_at=datetime.utcnow(),
    )
    db.add(assistant)
    db.flush()
    m = Membership(user_id=assistant.id, organization_id=org.id, role="teacher",
                   status="active", teacher_code="T-9002", position="보조")
    db.add(m)
    db.commit()
    client.patch(
        f"/api/v1/orgs/{org.id}/teachers/{m.id}",
        json={"role": "보조", "class_name": "1-1반"}, headers=auth(admin),
    )
    classes = client.get(f"/api/v1/orgs/{org.id}/classes", headers=auth(admin)).json()
    row = next(c for c in classes if c["name"] == "1-1반")
    assert row["assistant"] == "보조둘"
