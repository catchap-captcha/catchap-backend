"""학년부장(grade_head) — 임명/해임 + 학년 범위 스코프 검증."""

from datetime import datetime

from app.core.security import hash_password
from app.models import ClassRoom, Membership, User


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def _org_admin(db, org):
    admin = User(
        email="principal@test.dev",
        password_hash=hash_password("Password123!"),
        name="교장",
        role="org_admin",
        organization_id=org.id,
        email_verified_at=datetime.utcnow(),
    )
    db.add(admin)
    db.commit()
    return admin


def _login(client, email):
    res = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Password123!"},
    )
    return res.json()["access_token"]


def _teacher_membership_id(db, org):
    m = (
        db.query(Membership)
        .filter(Membership.organization_id == org.id, Membership.teacher_code == "T-1111")
        .first()
    )
    return m.id


def test_appoint_and_dismiss_grade_head(client, db, seed_org):
    org = seed_org["org"]
    _org_admin(db, org)
    admin_tok = _login(client, "principal@test.dev")
    mid = _teacher_membership_id(db, org)

    # 교장이 교사를 1학년 학년부장으로 임명
    res = client.post(
        f"/api/v1/orgs/{org.id}/teachers/{mid}/grade-head",
        json={"grade": 1},
        headers=auth(admin_tok),
    )
    assert res.status_code == 200, res.text
    assert res.json()["teacher"]["is_grade_head"] is True
    assert res.json()["teacher"]["managed_grade"] == 1

    # User.role 이 grade_head 로 승격됐는지 (로그인 진입 콘솔 결정)
    db.expire_all()
    u = db.query(User).filter(User.email == "t1@test.dev").first()
    assert u.role == "grade_head"

    # 목록에 노출
    heads = client.get(f"/api/v1/orgs/{org.id}/grade-heads", headers=auth(admin_tok)).json()
    assert any(h["managed_grade"] == 1 for h in heads)

    # 해임 → 교사로 강등
    res = client.request(
        "DELETE",
        f"/api/v1/orgs/{org.id}/teachers/{mid}/grade-head",
        headers=auth(admin_tok),
    )
    assert res.status_code == 200, res.text
    db.expire_all()
    u = db.query(User).filter(User.email == "t1@test.dev").first()
    assert u.role == "teacher"


def test_grade_head_scope_enforced(client, db, seed_org):
    org = seed_org["org"]
    _org_admin(db, org)
    admin_tok = _login(client, "principal@test.dev")
    mid = _teacher_membership_id(db, org)

    # 2학년 반 하나 + 그 반 학생 하나 추가 (범위 밖 대상)
    cls2 = ClassRoom(organization_id=org.id, name="2-1반", grade=2, status="active")
    db.add(cls2)
    db.flush()
    from app.models import StudentProfile

    stu2 = StudentProfile(
        organization_id=org.id,
        class_id=cls2.id,
        student_login_id="stu02",
        student_code="CAT-2222",
        password_hash=hash_password("1234"),
        nickname="2학년학생",
    )
    db.add(stu2)
    db.commit()

    # 1학년 학년부장 임명
    client.post(
        f"/api/v1/orgs/{org.id}/teachers/{mid}/grade-head",
        json={"grade": 1},
        headers=auth(admin_tok),
    )
    gh_tok = _login(client, "t1@test.dev")  # 이제 grade_head

    # 반 목록: 1학년 반만 보임 (2-1반 제외)
    classes = client.get(f"/api/v1/orgs/{org.id}/classes", headers=auth(gh_tok)).json()
    names = {c["name"] for c in classes}
    assert "1-1반" in names
    assert "2-1반" not in names

    # 담당 학년(1학년) 반 배정 → OK
    s1 = seed_org["student"].id
    ok = client.patch(
        f"/api/v1/orgs/{org.id}/students/{s1}/class",
        json={"class_label": "1-3반"},
        headers=auth(gh_tok),
    )
    assert ok.status_code == 200, ok.text

    # 다른 학년(2학년) 반으로 배정 → 403
    bad = client.patch(
        f"/api/v1/orgs/{org.id}/students/{s1}/class",
        json={"class_label": "2-5반"},
        headers=auth(gh_tok),
    )
    assert bad.status_code == 403, bad.text

    # 범위 밖(2학년) 학생을 1학년 반으로 끌어오기 → 배정 자체는 대상 학년(1학년)이라 허용되지만
    # 학년부장은 org 전체 관리(교장 전용)에는 접근 불가
    forbidden = client.post(
        f"/api/v1/orgs/{org.id}/teachers/{mid}/grade-head",
        json={"grade": 2},
        headers=auth(gh_tok),
    )
    assert forbidden.status_code == 403  # 임명은 교장 전용


def test_create_class_scope(client, db, seed_org):
    org = seed_org["org"]
    _org_admin(db, org)
    admin_tok = _login(client, "principal@test.dev")
    mid = _teacher_membership_id(db, org)

    # 교장은 아무 학년 반 생성 가능
    r = client.post(f"/api/v1/orgs/{org.id}/classes", json={"name": "3-4반"}, headers=auth(admin_tok))
    assert r.status_code == 200, r.text
    assert r.json()["class"]["grade"] == 3
    # 중복 생성 → 409
    assert client.post(f"/api/v1/orgs/{org.id}/classes", json={"name": "3-4반"}, headers=auth(admin_tok)).status_code == 409

    # 1학년 학년부장 임명 후
    client.post(f"/api/v1/orgs/{org.id}/teachers/{mid}/grade-head", json={"grade": 1}, headers=auth(admin_tok))
    gh = _login(client, "t1@test.dev")
    # 담당 학년(1) 반 생성 OK
    assert client.post(f"/api/v1/orgs/{org.id}/classes", json={"name": "1-9반"}, headers=auth(gh)).status_code == 200
    # 다른 학년(2) 반 생성 → 403
    assert client.post(f"/api/v1/orgs/{org.id}/classes", json={"name": "2-9반"}, headers=auth(gh)).status_code == 403
    # 학년 파싱 불가한 이름 → 403 (fail-closed)
    assert client.post(f"/api/v1/orgs/{org.id}/classes", json={"name": "특별반"}, headers=auth(gh)).status_code == 403


def test_dissolve_class(client, db, seed_org):
    org = seed_org["org"]
    _org_admin(db, org)
    admin = _login(client, "principal@test.dev")

    # 학생이 남아 있는 1-1반 해체 시도 → 409
    r = client.request("DELETE", f"/api/v1/orgs/{org.id}/classes/{seed_org['class'].id}", headers=auth(admin))
    assert r.status_code == 409, r.text

    # 빈 반 생성 → 해체 200 → 같은 이름 재생성이면 되살아남
    nid = client.post(f"/api/v1/orgs/{org.id}/classes", json={"name": "5-1반"}, headers=auth(admin)).json()["class"]["id"]
    assert client.request("DELETE", f"/api/v1/orgs/{org.id}/classes/{nid}", headers=auth(admin)).status_code == 200
    assert client.post(f"/api/v1/orgs/{org.id}/classes", json={"name": "5-1반"}, headers=auth(admin)).status_code == 200

    # 학년부장 스코프: 1학년 부장은 자기 학년 빈 반만 해체, 타 학년은 403
    mid = _teacher_membership_id(db, org)
    client.post(f"/api/v1/orgs/{org.id}/teachers/{mid}/grade-head", json={"grade": 1}, headers=auth(admin))
    gh = _login(client, "t1@test.dev")
    g1 = client.post(f"/api/v1/orgs/{org.id}/classes", json={"name": "1-8반"}, headers=auth(admin)).json()["class"]["id"]
    assert client.request("DELETE", f"/api/v1/orgs/{org.id}/classes/{g1}", headers=auth(gh)).status_code == 200
    g2 = client.post(f"/api/v1/orgs/{org.id}/classes", json={"name": "2-8반"}, headers=auth(admin)).json()["class"]["id"]
    assert client.request("DELETE", f"/api/v1/orgs/{org.id}/classes/{g2}", headers=auth(gh)).status_code == 403


def test_grade_head_fail_closed_on_unassigned_teacher(client, db, seed_org):
    """학급 미배정(grade=None) 교사는 학년부장이 수정/삭제 불가 (fail-closed)."""
    org = seed_org["org"]
    _org_admin(db, org)
    admin_tok = _login(client, "principal@test.dev")
    mid = _teacher_membership_id(db, org)
    client.post(f"/api/v1/orgs/{org.id}/teachers/{mid}/grade-head", json={"grade": 1}, headers=auth(admin_tok))
    gh = _login(client, "t1@test.dev")

    # 미클레임 교사 T-2222 (user_id=None, 담당 학급 없음 → grade None)
    from app.models import Membership

    unassigned = (
        db.query(Membership).filter(Membership.organization_id == org.id, Membership.teacher_code == "T-2222").first()
    )
    # 학년부장이 미배정 교사 수정 시도 → 403
    r = client.patch(
        f"/api/v1/orgs/{org.id}/teachers/{unassigned.id}", json={"role": "보조"}, headers=auth(gh)
    )
    assert r.status_code == 403, r.text
    # 삭제 시도 → 403
    r = client.request("DELETE", f"/api/v1/orgs/{org.id}/teachers/{unassigned.id}", headers=auth(gh))
    assert r.status_code == 403, r.text
    # 교장은 가능
    r = client.patch(
        f"/api/v1/orgs/{org.id}/teachers/{unassigned.id}", json={"role": "보조"}, headers=auth(admin_tok)
    )
    assert r.status_code == 200, r.text


def test_grade_head_register_unparseable_label_denied(client, db, seed_org):
    org = seed_org["org"]
    _org_admin(db, org)
    admin_tok = _login(client, "principal@test.dev")
    mid = _teacher_membership_id(db, org)
    client.post(f"/api/v1/orgs/{org.id}/teachers/{mid}/grade-head", json={"grade": 1}, headers=auth(admin_tok))
    gh = _login(client, "t1@test.dev")
    # 학년 파싱 불가한 반 이름으로 학생 등록 → 403 (fail-closed)
    r = client.post(
        f"/api/v1/orgs/{org.id}/students/register", json={"count": 1, "class_label": "특별반"}, headers=auth(gh)
    )
    assert r.status_code == 403, r.text
    # 담당 학년 반이면 OK
    r = client.post(
        f"/api/v1/orgs/{org.id}/students/register", json={"count": 1, "class_label": "1-5반"}, headers=auth(gh)
    )
    assert r.status_code == 200, r.text


def test_real_name_flow(client, db, seed_org):
    """기관이 실명과 함께 등록 → 활성화 시 real_name 복사 → 교사 화면 이름은 닉네임 변경과 무관."""
    org = seed_org["org"]
    _org_admin(db, org)
    admin = _login(client, "principal@test.dev")

    r = client.post(
        f"/api/v1/orgs/{org.id}/students/register",
        json={"count": 2, "class_label": "1-1반", "names": ["최진짜", "이실명"]},
        headers=auth(admin),
    )
    assert r.status_code == 200, r.text
    issued = r.json()["issued"]
    assert issued[0]["real_name"] == "최진짜"

    act = client.post(
        "/api/v1/auth/activate-student",
        json={"code": issued[0]["join_code"], "nickname": "반짝이", "password": "pw12345"},
    )
    assert act.status_code == 200, act.text

    from app.models import StudentProfile

    stu = db.query(StudentProfile).filter(StudentProfile.nickname == "반짝이").first()
    assert stu.real_name == "최진짜"  # 닉네임은 반짝이지만 실명 보존

    # 교사 화면(우리반)에는 실명으로 표시
    ttok = _login(client, "t1@test.dev")
    res = client.get("/api/v1/teacher/class/students", headers=auth(ttok)).json()
    names = [s["name"] for s in res["students"]]
    assert "최진짜" in names
    assert "반짝이" not in names  # 닉네임이 아니라 실명 노출

    # 학생 자신의 화면(랭킹)에는 여전히 닉네임만
    stok = client.post(
        "/api/v1/auth/student-login",
        json={"organization_id": org.id, "student_login_id": stu.student_login_id, "password": "pw12345"},
    ).json()["access_token"]
    board = client.get("/api/v1/students/me/class-ranking", headers=auth(stok)).json()["board"]
    board_names = [b["name"] for b in board]
    assert "반짝이" in board_names
    assert "최진짜" not in board_names  # 실명은 학생 화면에 노출 금지


def test_register_by_label_binds_real_class(client, db, seed_org):
    """반 이름만으로 학생 등록 → 실제 학급 생성/연결 → 활성화 시 그 반에 배정된다."""
    org = seed_org["org"]
    _org_admin(db, org)
    admin = _login(client, "principal@test.dev")

    r = client.post(
        f"/api/v1/orgs/{org.id}/students/register",
        json={"count": 1, "class_label": "4-4반"},
        headers=auth(admin),
    )
    assert r.status_code == 200, r.text
    join_code = r.json()["issued"][0]["join_code"]

    from app.models import ClassRoom, StudentProfile

    cls = db.query(ClassRoom).filter(ClassRoom.organization_id == org.id, ClassRoom.name == "4-4반").first()
    assert cls is not None and cls.grade == 4  # 반이 실제로 생성됨

    act = client.post(
        "/api/v1/auth/activate-student",
        json={"code": join_code, "nickname": "신입이", "password": "newpass123"},
    )
    assert act.status_code == 200, act.text
    db.expire_all()
    stu = db.query(StudentProfile).filter(StudentProfile.nickname == "신입이").first()
    assert stu.class_id == cls.id  # 활성화된 학생이 그 반에 배정됨


def test_grade_head_cannot_use_org_admin_only(client, db, seed_org):
    org = seed_org["org"]
    _org_admin(db, org)
    admin_tok = _login(client, "principal@test.dev")
    mid = _teacher_membership_id(db, org)
    client.post(
        f"/api/v1/orgs/{org.id}/teachers/{mid}/grade-head",
        json={"grade": 1},
        headers=auth(admin_tok),
    )
    gh_tok = _login(client, "t1@test.dev")
    # 캡차 설정(교장 전용) 접근 불가
    assert (
        client.get(f"/api/v1/orgs/{org.id}/captcha-settings", headers=auth(gh_tok)).status_code
        == 403
    )
    # 학년부장 목록(교장 전용) 접근 불가
    assert (
        client.get(f"/api/v1/orgs/{org.id}/grade-heads", headers=auth(gh_tok)).status_code == 403
    )
