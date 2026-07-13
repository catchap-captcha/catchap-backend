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


def test_parent_child_limit(client, db, seed_org):
    """자녀 연결 수 상한 없음(구 2명 상한은 0713 제품 결정으로 제거) — 3명도 전부 연결된다."""
    from datetime import datetime

    from app.core.security import hash_password
    from app.models import StudentProfile, User
    from app.services import onboarding_service

    parent = User(
        email="p2@test.dev",
        password_hash=hash_password("Password123!"),
        name="상한테스트학부모",
        role="parent",
        email_verified_at=datetime.utcnow(),
    )
    db.add(parent)
    # 자녀 후보 3명 생성
    kids = []
    for i in range(3):
        kid = StudentProfile(
            organization_id=seed_org["org"].id,
            student_login_id=f"cap0{i}",
            student_code=f"CAT-90{i}0",
            password_hash=hash_password("1234"),
            nickname=f"자녀{i}",
        )
        db.add(kid)
        kids.append(kid)
    db.commit()

    token = client.post(
        "/api/v1/auth/login",
        json={"role": "parent", "email": "p2@test.dev", "password": "Password123!"},
    ).json()["access_token"]

    def link(kid):
        code = onboarding_service.issue_parent_invite(
            db, student_id=kid.id, organization_id=seed_org["org"].id
        )
        return client.post(
            "/api/v1/parents/me/children/link-invite",
            json={"invite_code": code},
            headers=auth(token),
        )

    assert link(kids[0]).status_code == 200
    assert link(kids[1]).status_code == 200
    assert link(kids[2]).status_code == 200  # 다자녀 — 상한 없이 연결
    children = client.get("/api/v1/parents/me/children", headers=auth(token))
    assert children.status_code == 200 and len(children.json()) == 3


def test_ops_cannot_access_org_scoped_data(client, db, seed_org):
    """운영자(ops)는 기관 스코프 API(학생 명단·기관 대시보드)에 접근하지 못한다 — 아동 PII 분리.

    운영자는 /ops/* 콘솔의 익명·집계 데이터만 보고, 학생 실명·나이가 노출되는
    /orgs/{org_id}/* 는 기관 관리자/학년부장에게만 허용된다.
    """
    from datetime import datetime

    from app.core.security import hash_password
    from app.models import User

    ops = User(
        email="ops-rbac@test.dev",
        password_hash=hash_password("Password123!"),
        name="운영자",
        role="ops",
        status="active",
        email_verified_at=datetime.utcnow(),
    )
    db.add(ops)
    db.commit()
    token = client.post(
        "/api/v1/auth/ops-login",
        json={"email": "ops-rbac@test.dev", "password": "Password123!"},
    ).json()["access_token"]
    oid = seed_org["org"].id
    assert client.get(f"/api/v1/orgs/{oid}/roster", headers=auth(token)).status_code == 403
    assert client.get(f"/api/v1/orgs/{oid}/dashboard", headers=auth(token)).status_code == 403
    # 운영자 콘솔(익명 집계)은 계속 접근 가능
    assert client.get("/api/v1/ops/orgs", headers=auth(token)).status_code == 200
