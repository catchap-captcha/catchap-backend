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


def test_parent_can_link_more_than_two_children(client, db, seed_org):
    """자녀 연결 수 상한 제거 회귀 — 3명째 연결도 성공해야 한다(구 2명 409)."""
    from app.core.security import hash_password
    from app.models import StudentProfile
    from app.services import onboarding_service

    org = seed_org["org"]
    parent = _mk_user(db, None, "many@test.dev", "parent", "다둥이맘")
    assert parent is not None
    students = [seed_org["student"]]
    for i in (2, 3):
        s = StudentProfile(
            organization_id=org.id,
            class_id=seed_org["class"].id,
            student_login_id=f"stu0{i}x",
            student_code=f"CAT-222{i}",
            password_hash=hash_password("1234"),
            nickname=f"자녀{i}",
        )
        db.add(s)
        db.commit()
        students.append(s)

    token = _login(client, "parent", "many@test.dev")
    for s in students:
        raw = onboarding_service.issue_parent_invite(
            db, student_id=s.id, organization_id=org.id, created_by=seed_org["teacher"].id
        )
        db.commit()
        res = client.post(
            "/api/v1/parents/me/children/link-invite",
            json={"invite_code": raw},
            headers=auth(token),
        )
        assert res.status_code == 200, f"{s.nickname} 연결 실패: {res.text}"

    children = client.get("/api/v1/parents/me/children", headers=auth(token))
    assert children.status_code == 200
    assert len(children.json()) >= 3


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
    # 캡차 엔진은 실제 로드된 문항 수 — 0문항이면 키 조회 버그(과목명은 한국어)
    assert names["captcha-engine"]["status"] == "ok", names["captcha-engine"]
    import re
    m = re.search(r"출제 가능 (\d+)문항", names["captcha-engine"]["detail"] or "")
    assert m and int(m.group(1)) > 0, names["captcha-engine"]["detail"]
    # SMTP 미설정(테스트 env)은 dry-run으로 정직하게
    assert names["smtp"]["status"] in ("ok", "degraded", "dry-run")
    assert body["checked_at"]


def test_ops_list_pagination_and_backcompat(client, db, seed_org):
    """orgs/api-keys/registration-requests — page 없으면 배열(하위호환), 있으면 페이지 응답"""
    _mk_user(db, None, "listops@test.dev", "ops", "운영자")
    res = client.post(
        "/api/v1/auth/ops-login",
        json={"email": "listops@test.dev", "password": "Password123!"},
    )
    headers = auth(res.json()["access_token"])

    for path in ("/api/v1/ops/orgs", "/api/v1/ops/api-keys", "/api/v1/ops/registration-requests"):
        legacy = client.get(path, headers=headers)
        assert legacy.status_code == 200, (path, legacy.text)
        assert isinstance(legacy.json(), list), f"{path}: page 없으면 배열이어야 한다(하위호환)"

        paged = client.get(path, params={"page": 1, "page_size": 10}, headers=headers)
        body = paged.json()
        assert isinstance(body, dict) and "items" in body and "total" in body, path
        assert body["page"] == 1

    # orgs 검색 + 전체 집계 필드
    r = client.get("/api/v1/ops/orgs", params={"page": 1, "search": "테스트초등"}, headers=headers)
    b = r.json()
    assert b["total"] == 1 and b["items"][0]["name"] == "테스트초등학교"
    assert b["total_all"] >= 1 and "total_students" in b

    # registration-requests 페이지 응답엔 탭 배지 counts
    r2 = client.get("/api/v1/ops/registration-requests", params={"page": 1}, headers=headers)
    assert set(r2.json()["counts"]) == {"pending", "approved", "rejected"}


def test_ops_ai_model_crud_reflects_in_org_console(client, db, seed_org):
    """운영자 모델 등록/수정이 기관 콘솔 AI 모델 화면에 그대로 반영 + 감사 기록"""
    from app.models import AuditLog

    _mk_user(db, None, "aiops@test.dev", "ops", "운영자")
    _mk_user(db, seed_org["org"].id, "aiadmin@test.dev", "org_admin", "김교장")
    ops_token = client.post(
        "/api/v1/auth/ops-login",
        json={"email": "aiops@test.dev", "password": "Password123!"},
    ).json()["access_token"]

    # 등록
    r = client.post(
        "/api/v1/ops/ai-models",
        json={"category": "행동 판정", "name": "KidGuard", "provider": "CatChap",
              "version": "v0.1", "status": "베타", "description": "아동 보정 봇 판정"},
        headers=auth(ops_token),
    )
    assert r.status_code == 200, r.text
    mid = r.json()["id"]

    # 수정 (상태 정상 승격)
    r2 = client.patch(
        f"/api/v1/ops/ai-models/{mid}",
        json={"category": "행동 판정", "name": "KidGuard", "provider": "CatChap",
              "version": "v0.2", "status": "정상", "description": "아동 보정 봇 판정"},
        headers=auth(ops_token),
    )
    assert r2.status_code == 200 and r2.json()["version"] == "v0.2"

    # 기관 콘솔 화면에 반영되는지
    admin_token = _login(client, "org_admin", "aiadmin@test.dev")
    org_view = client.get(
        f"/api/v1/orgs/{seed_org['org'].id}/ai-models", headers=auth(admin_token)
    ).json()
    mine = [m for m in org_view["models"] if m["id"] == mid]
    assert mine and mine[0]["version"] == "v0.2" and mine[0]["status"] == "정상"

    # 감사 기록
    actions = {a for (a,) in db.query(AuditLog.action).all()}
    assert {"ops.ai_model_create", "ops.ai_model_update"} <= actions
