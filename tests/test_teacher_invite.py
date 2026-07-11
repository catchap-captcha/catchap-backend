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
    # 선발급 교사코드 멤버십(pending). 이름을 넣으면 표시용 placeholder User(pending)를
    # 만들어 이름을 보관한다(add_teacher와 동일 패턴). 직책(position)엔 이름이 섞이지 않는다.
    m = db.query(Membership).filter(Membership.teacher_code == inv.teacher_code).first()
    assert m is not None and m.status == "pending"
    assert m.position is None
    assert m.user_id is not None
    ph = db.get(User, m.user_id)
    assert ph is not None and ph.status == "pending" and ph.name == "김새샘"

    # 토큰 원문은 메일로만 가므로, 서비스로 새 초대를 만들어 GET /auth/invite/{token} 성공 경로 검증
    from app.services import invite_service

    raw = invite_service.create_teacher_invite(
        db,
        organization_id=org_id,
        inviter_id=m.id,
        email="second@test.dev",
        name="이둘째",
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
    # 초대 시 입력한 이름이 프리필로 반환돼 가입화면 이름칸이 자동 입력된다
    assert body["name"] == "이둘째"


def test_invite_with_class_auto_assigns_on_register(client, db, seed_org):
    """초대 시 담당 학급을 미리 지정하면 pending_class로 예약되고, 가입 시 그 반 담임으로 자동 배정된다."""
    from app.services import invite_service
    from app.models import ClassRoom, User

    _make_org_admin(db, seed_org)
    org_id = seed_org["org"].id

    raw = invite_service.create_teacher_invite(
        db, organization_id=org_id, inviter_id="x",
        email="pre@test.dev", name="예약담임", role="teacher", class_name="3-7반",
    )
    db.commit()
    inv = db.query(Invitation).filter(Invitation.email == "pre@test.dev").first()
    m = db.query(Membership).filter(Membership.teacher_code == inv.teacher_code).first()
    assert m.pending_class == "3-7반"  # 예약됨

    res = client.post(
        "/api/v1/auth/register/teacher",
        json={"name": "예약담임", "email": "pre@test.dev", "password": "Teacher!234",
              "organization_id": org_id, "teacher_code": inv.teacher_code, "invite_token": raw},
    )
    assert res.status_code == 200, res.text
    db.expire_all()
    u = db.query(User).filter(User.email == "pre@test.dev").first()
    cls = db.query(ClassRoom).filter(ClassRoom.organization_id == org_id, ClassRoom.name == "3-7반").first()
    assert cls is not None and cls.teacher_id == u.id  # 가입 즉시 담임 배정
    db.refresh(m)
    assert m.pending_class is None  # 배정 후 예약 비움


def test_invite_register_skips_email_code_and_shows_role(client, db, seed_org):
    """초대 링크로 온 교사는 이메일 인증코드 없이 가입되고(초대 토큰이 이메일 소유 증명),
    교사 목록에 이름과 역할('교사'/'학년부장')이 바르게 뜬다. 이름이 역할칸에 새지 않는다."""
    from app.services import invite_service

    _make_org_admin(db, seed_org)
    admin_token = _login(client, "principal@test.dev")
    org_id = seed_org["org"].id

    # 1) 일반 교사 초대 → 초대 토큰으로 이메일 코드 없이 가입
    raw = invite_service.create_teacher_invite(
        db, organization_id=org_id, inviter_id="x",
        email="claim@test.dev", name="박클레임", role="teacher",
    )
    db.commit()
    inv = db.query(Invitation).filter(Invitation.email == "claim@test.dev").first()
    res = client.post(
        "/api/v1/auth/register/teacher",
        json={
            "name": "박클레임", "email": "claim@test.dev", "password": "Teacher!234",
            "organization_id": org_id, "teacher_code": inv.teacher_code,
            "invite_token": raw,  # email_code 생략
        },
    )
    assert res.status_code == 200, res.text
    claimed = db.query(User).filter(User.email == "claim@test.dev").first()
    assert claimed is not None and claimed.status == "active" and claimed.role == "teacher"

    # 2) 잘못된/없는 초대 토큰이면 이메일 코드가 필요해 실패한다(우회는 유효 토큰 한정)
    raw2 = invite_service.create_teacher_invite(
        db, organization_id=org_id, inviter_id="x",
        email="nocode@test.dev", name="무코드", role="teacher",
    )
    db.commit()
    inv2 = db.query(Invitation).filter(Invitation.email == "nocode@test.dev").first()
    bad = client.post(
        "/api/v1/auth/register/teacher",
        json={
            "name": "무코드", "email": "nocode@test.dev", "password": "Teacher!234",
            "organization_id": org_id, "teacher_code": inv2.teacher_code,
            # invite_token 없음, email_code 없음 → 인증코드 검사에서 실패해야 함
        },
    )
    assert bad.status_code != 200

    # 3) 교사 목록: 가입한 교사는 이름·역할 '교사', 미가입 초대 교사도 이름이 보인다
    lst = client.get(f"/api/v1/orgs/{org_id}/teachers", headers=auth(admin_token))
    assert lst.status_code == 200, lst.text
    rows = {r["name"]: r for r in lst.json()}
    assert "박클레임" in rows and rows["박클레임"]["role"] == "교사"
    assert rows["박클레임"]["status"] == "active"
    assert "무코드" in rows  # 미가입(초대 대기)이어도 이름이 뜬다
    assert rows["무코드"]["role"] == "교사"  # 이름이 역할칸에 새지 않는다


def test_grade_head_invite_role_label(client, db, seed_org):
    """학년부장으로 초대·가입하면 목록 역할이 '학년부장'으로 뜬다(이름이 아님)."""
    from app.services import invite_service

    _make_org_admin(db, seed_org)
    admin_token = _login(client, "principal@test.dev")
    org_id = seed_org["org"].id
    raw = invite_service.create_teacher_invite(
        db, organization_id=org_id, inviter_id="x",
        email="gh@test.dev", name="정부장", role="grade_head",
    )
    db.commit()
    inv = db.query(Invitation).filter(Invitation.email == "gh@test.dev").first()
    res = client.post(
        "/api/v1/auth/register/teacher",
        json={
            "name": "정부장", "email": "gh@test.dev", "password": "Teacher!234",
            "organization_id": org_id, "teacher_code": inv.teacher_code, "invite_token": raw,
        },
    )
    assert res.status_code == 200, res.text
    lst = client.get(f"/api/v1/orgs/{org_id}/teachers", headers=auth(admin_token))
    rows = {r["name"]: r for r in lst.json()}
    assert "정부장" in rows and rows["정부장"]["role"] == "학년부장"


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
