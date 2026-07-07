"""RBAC — 역할별 접근 제한이 API 단계에서 강제되는지 검증"""


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


def _teacher_token(client):
    res = client.post(
        "/api/v1/auth/login",
        json={"role": "teacher", "email": "t1@test.dev", "password": "Password123!"},
    )
    return res.json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_unauthenticated_rejected(client, seed_org):
    assert client.get("/api/v1/students/me/dashboard").status_code in (401, 403)
    assert client.get("/api/v1/teacher/dashboard").status_code in (401, 403)
    assert client.get("/api/v1/notifications").status_code in (401, 403)


def test_student_cannot_access_teacher_api(client, seed_org):
    token = _student_token(client, seed_org)
    assert client.get("/api/v1/teacher/dashboard", headers=auth(token)).status_code == 403
    assert client.get("/api/v1/parents/me/children", headers=auth(token)).status_code == 403
    assert (
        client.get(f"/api/v1/orgs/{seed_org['org'].id}/dashboard", headers=auth(token)).status_code
        == 403
    )


def test_teacher_cannot_access_student_or_org_api(client, seed_org):
    token = _teacher_token(client)
    assert client.get("/api/v1/students/me/dashboard", headers=auth(token)).status_code == 403
    assert client.get("/api/v1/ops/dashboard", headers=auth(token)).status_code == 403


def test_org_scope_enforced(client, db, seed_org):
    """다른 기관 관리자의 내 기관 데이터 접근 차단"""
    from datetime import datetime

    from app.core.security import hash_password
    from app.models import Organization, User

    other_org = Organization(name="다른기관", code="XX-EDU-9999", org_type="유치원")
    db.add(other_org)
    db.flush()
    other_admin = User(
        email="other-admin@test.dev",
        password_hash=hash_password("Password123!"),
        name="타기관관리자",
        role="org_admin",
        organization_id=other_org.id,
        email_verified_at=datetime.utcnow(),
    )
    db.add(other_admin)
    db.commit()

    res = client.post(
        "/api/v1/auth/login",
        json={"role": "org_admin", "email": "other-admin@test.dev", "password": "Password123!"},
    )
    token = res.json()["access_token"]
    assert (
        client.get(f"/api/v1/orgs/{seed_org['org'].id}/dashboard", headers=auth(token)).status_code
        == 403
    )


def test_parent_only_linked_children(client, db, seed_org):
    from datetime import datetime

    from app.core.security import hash_password
    from app.models import User

    parent = User(
        email="p1@test.dev",
        password_hash=hash_password("Password123!"),
        name="테스트학부모",
        role="parent",
        email_verified_at=datetime.utcnow(),
    )
    db.add(parent)
    db.commit()

    res = client.post(
        "/api/v1/auth/login",
        json={"role": "parent", "email": "p1@test.dev", "password": "Password123!"},
    )
    token = res.json()["access_token"]

    # 연결 전: 자녀 요약 접근 불가
    sid = seed_org["student"].id
    assert (
        client.get(f"/api/v1/parents/me/children/{sid}/summary", headers=auth(token)).status_code
        == 403
    )

    # 학교 발급 초대코드로만 연결 (학생코드 자동승인은 폐지됨 — B1)
    from app.services import onboarding_service

    invite = onboarding_service.issue_parent_invite(
        db, student_id=sid, organization_id=seed_org["org"].id
    )
    link = client.post(
        "/api/v1/parents/me/children/link-invite",
        json={"invite_code": invite},
        headers=auth(token),
    )
    assert link.status_code == 200, link.text
    assert (
        client.get(f"/api/v1/parents/me/children/{sid}/summary", headers=auth(token)).status_code
        == 200
    )

    # 잘못된 초대코드 404
    assert (
        client.post(
            "/api/v1/parents/me/children/link-invite",
            json={"invite_code": "LINK-ZZZZ-ZZZZ"},
            headers=auth(token),
        ).status_code
        == 404
    )
    # 새 초대코드로 다시 연결 시도 → 이미 연결된 자녀 409
    invite2 = onboarding_service.issue_parent_invite(
        db, student_id=sid, organization_id=seed_org["org"].id
    )
    assert (
        client.post(
            "/api/v1/parents/me/children/link-invite",
            json={"invite_code": invite2},
            headers=auth(token),
        ).status_code
        == 409
    )
