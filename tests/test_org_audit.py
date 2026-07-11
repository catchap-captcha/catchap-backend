"""기관 활동 기록(GET /orgs/{org_id}/audit-logs) — 스코프·ops 제외·익명화 검증"""

from datetime import datetime

from app.core.security import hash_password


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def _mk_user(db, org_id, email, role, name):
    from app.models import Membership, User

    u = User(
        email=email,
        password_hash=hash_password("Password123!"),
        name=name,
        role=role,
        organization_id=org_id,
        email_verified_at=datetime.utcnow(),
    )
    db.add(u)
    db.flush()
    if org_id:
        db.add(Membership(user_id=u.id, organization_id=org_id, role=role, status="active"))
    db.commit()
    return u


def _login(client, role, email):
    res = client.post(
        "/api/v1/auth/login",
        json={"role": role, "email": email, "password": "Password123!"},
    )
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def _seed_logs(db, org_id, admin_id, teacher_id, ops_id, student_id):
    from app.utils.helpers import audit

    audit(db, action="org.teacher_invite", actor_user_id=admin_id,
          organization_id=org_id, target_type="invitation", target_id="i1")
    audit(db, action="student.parent_invite", actor_user_id=teacher_id,
          organization_id=org_id, target_type="student", target_id="s1")
    audit(db, action="settings.change_password", actor_user_id=student_id,
          organization_id=org_id, target_type="student", target_id=student_id)
    # 운영자 내부 행위 — 기관 화면에 노출되면 안 됨
    audit(db, action="ops.org_update", actor_user_id=ops_id,
          organization_id=org_id, target_type="organization", target_id=org_id)
    db.commit()


def test_org_admin_sees_own_org_logs_without_ops_actions(client, db, seed_org):
    org = seed_org["org"]
    admin = _mk_user(db, org.id, "admin@test.dev", "org_admin", "김교장")
    ops = _mk_user(db, None, "ops@test.dev", "ops", "운영자")
    _seed_logs(db, org.id, admin.id, seed_org["teacher"].id, ops.id, seed_org["student"].id)

    token = _login(client, "org_admin", "admin@test.dev")
    res = client.get(f"/api/v1/orgs/{org.id}/audit-logs", headers=auth(token))
    assert res.status_code == 200, res.text
    body = res.json()
    actions = [i["action"] for i in body["items"]]
    # 기관 구성원 행위는 보이고
    assert "org.teacher_invite" in actions
    assert "student.parent_invite" in actions
    assert "settings.change_password" in actions
    # 운영자 행위는 숨겨진다
    assert "ops.org_update" not in actions
    assert "ops.org_update" not in body["actions"]  # facet에서도 제외
    assert body["total"] == 3


def test_org_audit_student_actor_is_anonymized(client, db, seed_org):
    org = seed_org["org"]
    _mk_user(db, org.id, "admin@test.dev", "org_admin", "김교장")
    _seed_logs(db, org.id, None, None, None, seed_org["student"].id)

    token = _login(client, "org_admin", "admin@test.dev")
    res = client.get(f"/api/v1/orgs/{org.id}/audit-logs", headers=auth(token))
    rows = [i for i in res.json()["items"] if i["action"] == "settings.change_password"]
    assert rows, "학생 self-service 로그가 있어야 한다"
    actor = rows[0]["actor_name"]
    # 닉네임("테스트학생")이 아니라 익명 코드로만 표시
    assert actor is not None and actor.startswith("학생 ")
    assert "테스트학생" not in actor
    assert rows[0]["actor_email"] is None


def test_org_audit_scope_and_role_enforced(client, db, seed_org):
    from app.models import Organization

    org = seed_org["org"]
    other = Organization(name="다른학교", code="TS-EDU-2000", org_type="초등학교")
    db.add(other)
    db.commit()
    _mk_user(db, org.id, "admin@test.dev", "org_admin", "김교장")

    admin_token = _login(client, "org_admin", "admin@test.dev")
    # 타 기관 조회 불가
    assert (
        client.get(f"/api/v1/orgs/{other.id}/audit-logs", headers=auth(admin_token)).status_code
        == 403
    )
    # 교사는 기관 감사기록 접근 불가
    teacher_token = _login(client, "teacher", "t1@test.dev")
    assert (
        client.get(f"/api/v1/orgs/{org.id}/audit-logs", headers=auth(teacher_token)).status_code
        == 403
    )


def test_parent_child_link_is_audited(client, db, seed_org):
    """학부모 자녀 연결(민감 행위)이 감사에 남는지 — 이전 누락 버그 회귀 방지"""
    from app.models import AuditLog
    from app.services import onboarding_service

    org = seed_org["org"]
    student = seed_org["student"]
    parent = _mk_user(db, None, "p1@test.dev", "parent", "학부모일")

    raw_code = onboarding_service.issue_parent_invite(
        db, student_id=student.id, organization_id=org.id, created_by=seed_org["teacher"].id
    )
    db.commit()

    token = _login(client, "parent", "p1@test.dev")
    res = client.post(
        "/api/v1/parents/me/children/link-invite",
        json={"invite_code": raw_code},
        headers=auth(token),
    )
    assert res.status_code == 200, res.text

    row = db.query(AuditLog).filter(AuditLog.action == "parent.child_link").first()
    assert row is not None, "자녀 연결이 감사로그에 남아야 한다"
    assert row.actor_user_id == parent.id  # 행위자 = 학부모 (대상 오귀속 금지)
    assert row.organization_id == org.id
    assert row.target_id == student.id


def test_ops_system_health_is_measured_not_stub(client, db, seed_org):
    """/ops/system이 하드코딩 스텁이 아니라 실측을 반환하는지"""
    ops = _mk_user(db, None, "sysops@test.dev", "ops", "운영자")
    assert ops is not None
    res = client.post(
        "/api/v1/auth/ops-login",
        json={"email": "sysops@test.dev", "password": "Password123!"},
    )
    token = res.json()["access_token"]
    r = client.get("/api/v1/ops/system", headers=auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    names = {s["name"]: s for s in body["services"]}
    # 필수 서비스 카드
    assert {"db", "captcha-engine", "smtp", "disk", "ai-server"} <= set(names)
    # DB는 실측 왕복 — ok에 양수 레이턴시 (예전 스텁은 상수 6)
    assert names["db"]["status"] == "ok" and names["db"]["latency_ms"] >= 1
    # 캡차 엔진은 실제 문항 수를 detail로
    assert names["captcha-engine"]["status"] in ("ok", "degraded")
    assert "문항" in (names["captcha-engine"]["detail"] or "")
    # SMTP 미설정(테스트 env)은 dry-run으로 정직하게
    assert names["smtp"]["status"] in ("ok", "degraded", "dry-run")
    assert body["checked_at"]
