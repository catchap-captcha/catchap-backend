"""교사 초대링크·담임 비번 초기화 — 성공 경로 커버 (이식 검증에서 공백으로 지적된 부분)."""

from app.core.security import hash_password
from app.models import Invitation, Membership, User


def _login(client, email, password="Password123!"):
    res = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def _make_org_admin(db, seed_org):
    admin = User(
        email="principal@test.dev",
        password_hash=hash_password("Password123!"),
        name="테스트교장",
        role="org_admin",
        organization_id=seed_org["org"].id,
        email_verified_at=__import__("datetime").datetime.utcnow(),
    )
    db.add(admin)
    db.flush()
    db.add(
        Membership(
            user_id=admin.id,
            organization_id=seed_org["org"].id,
            role="org_admin",
            status="active",
        )
    )
    db.commit()
    return admin


def test_teacher_invite_success_path(client, db, seed_org):
    """초대 발송 → Invitation(선발급 코드) 생성 → 토큰 검증이 기관·코드를 프리필로 반환."""
    _make_org_admin(db, seed_org)
    token = _login(client, "principal@test.dev")

    org_id = seed_org["org"].id
    res = client.post(
        f"/api/v1/orgs/{org_id}/teacher-invites",
        json={"email": "newteacher@test.dev", "name": "김새샘", "role": "teacher"},
        headers=auth(token),
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "email": "newteacher@test.dev"}
    # 응답에 토큰이 노출되지 않아야 한다 (메일로만 전달)
    assert "token" not in res.json()

    inv = db.query(Invitation).filter(Invitation.email == "newteacher@test.dev").first()
    assert inv is not None and inv.status == "pending"
    assert inv.teacher_code and inv.teacher_code.startswith("T-")
    # 선발급 교사코드 멤버십 (user_id=NULL, pending)
    m = db.query(Membership).filter(Membership.teacher_code == inv.teacher_code).first()
    assert m is not None and m.user_id is None and m.status == "pending"

    # 토큰 원문은 메일로만 가므로, 서비스로 새 초대를 만들어 GET /auth/invite/{token} 성공 경로 검증
    from app.services import invite_service

    raw = invite_service.create_teacher_invite(
        db,
        organization_id=org_id,
        inviter_id=m.id,
        email="second@test.dev",
        role="teacher",
    )
    db.commit()
    got = client.get(f"/api/v1/auth/invite/{raw}")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["valid"] is True
    assert body["organization_id"] == org_id
    assert body["email"] == "second@test.dev"
    assert body["teacher_code"].startswith("T-")


def test_teacher_reset_student_password(client, db, seed_org):
    """담임은 자기 반 학생 비번 초기화 가능(임시비번+강제변경), 교장은 403."""
    token = _login(client, "t1@test.dev")
    sid = seed_org["student"].id
    res = client.post(
        f"/api/v1/teacher/class/students/{sid}/reset-password", headers=auth(token)
    )
    assert res.status_code == 200, res.text
    temp = res.json()["temp_password"]
    assert temp.startswith("cat-")
    db.refresh(seed_org["student"])
    assert seed_org["student"].must_change_password is True

    # 교장(org_admin)은 담임 경로로 초기화할 수 없다
    _make_org_admin(db, seed_org)
    admin_token = _login(client, "principal@test.dev")
    res2 = client.post(
        f"/api/v1/teacher/class/students/{sid}/reset-password", headers=auth(admin_token)
    )
    assert res2.status_code == 403
