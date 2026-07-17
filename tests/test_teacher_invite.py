"""교사 초대 — 제품 전환(학교 기능 은퇴, 2026-07-17)으로 발급·가입 모두 410.

종전 성공 경로 테스트(초대 발송→토큰 검증→클레임 가입→학급 자동배정)는 접수 종료로
은퇴했다 — 3단계 정리에서 invite_service의 죽은 코드와 함께 제거한다.
담임의 학부모 초대·비번 초기화는 살아있는 기능이라 그대로 검증한다."""

import pytest
from fastapi import HTTPException

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


def test_teacher_invite_retired(client, db, seed_org):
    """기관 관리자가 초대를 보내도 410 — Invitation·pending 멤버십이 생기지 않는다."""
    _make_org_admin(db, seed_org)
    token = _login(client, "principal@test.dev")

    org_id = seed_org["org"].id
    res = client.post(
        f"/api/v1/orgs/{org_id}/teacher-invites",
        json={"email": "newteacher@test.dev", "name": "김새샘", "role": "teacher"},
        headers=auth(token),
    )
    assert res.status_code == 410, res.text
    assert db.query(Invitation).filter(Invitation.email == "newteacher@test.dev").first() is None

    # 서비스 직접 호출도 동일하게 거부 (다른 호출부가 생겨도 벽은 서비스에 있다)
    from app.services import invite_service

    with pytest.raises(HTTPException) as e:
        invite_service.create_teacher_invite(
            db, organization_id=org_id, inviter_id="x", email="second@test.dev", role="teacher"
        )
    assert e.value.status_code == 410


def test_teacher_issues_parent_invite_for_own_class(client, db, seed_org):
    """담임이 자기 반 학생의 학부모 초대 코드를 발급할 수 있다(코드 원문 1회 반환)."""
    tok = _login(client, "t1@test.dev")
    sid = seed_org["student"].id
    r = client.post(f"/api/v1/teacher/class/students/{sid}/invite-code", headers=auth(tok))
    assert r.status_code == 200, r.text
    assert r.json()["invite_code"].startswith("LINK-")
    # 존재하지 않는/타 반 학생 → 403
    bad = client.post("/api/v1/teacher/class/students/00000000-0000-0000-0000-000000000000/invite-code",
                      headers=auth(tok))
    assert bad.status_code == 403


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
