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
        json={
            "code": issued[0]["join_code"],
            "student_login_id": "banjjak1",
            "nickname": "반짝이",
            "password": "pw12345",
        },
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
        json={
            "code": join_code,
            "student_login_id": "newbie1",
            "nickname": "신입이",
            "password": "newpass123",
        },
    )
    assert act.status_code == 200, act.text
    db.expire_all()
    stu = db.query(StudentProfile).filter(StudentProfile.nickname == "신입이").first()
    assert stu.class_id == cls.id  # 활성화된 학생이 그 반에 배정됨


def test_register_with_genders_persists_and_copies_on_activation(client, db, seed_org):
    """학생 추가 시 성별(이름 순서대로)을 넣으면 슬롯에 저장되고, 활성화 시 학생 프로필로 복사된다.
    (프론트 학생추가 모달이 보내는 payload 계약 검증 — 이름/성별 정렬)"""
    org = seed_org["org"]
    _org_admin(db, org)
    admin = _login(client, "principal@test.dev")

    r = client.post(
        f"/api/v1/orgs/{org.id}/students/register",
        json={
            "count": 2,
            "class_label": "3-1반",
            "names": ["김하은", "박도윤"],
            "genders": ["female", "male"],  # 이름 순서대로
        },
        headers=auth(admin),
    )
    assert r.status_code == 200, r.text
    issued = r.json()["issued"]
    assert len(issued) == 2
    # 슬롯에 성별이 이름 순서대로 저장됨
    by_name = {it.get("real_name"): it for it in issued}
    assert by_name["김하은"]["gender"] == "female"
    assert by_name["박도윤"]["gender"] == "male"

    # 활성화하면 학생 프로필 gender로 복사(아이는 못 고름)
    from app.models import StudentProfile

    act = client.post(
        "/api/v1/auth/activate-student",
        json={
            "code": by_name["김하은"]["join_code"],
            "student_login_id": "haeun01",
            "nickname": "하은이",
            "password": "newpass123",
        },
    )
    assert act.status_code == 200, act.text
    db.expire_all()
    stu = db.query(StudentProfile).filter(StudentProfile.nickname == "하은이").first()
    assert stu is not None and stu.gender == "female"


def test_register_without_genders_leaves_none(client, db, seed_org):
    """성별 미지정(genders 생략)이면 None으로 남는다 — 강제 입력 아님."""
    org = seed_org["org"]
    _org_admin(db, org)
    admin = _login(client, "principal@test.dev")
    r = client.post(
        f"/api/v1/orgs/{org.id}/students/register",
        json={"count": 1, "class_label": "3-2반", "names": ["미정이"]},
        headers=auth(admin),
    )
    assert r.status_code == 200, r.text
    assert r.json()["issued"][0]["gender"] is None


def test_verify_join_code_does_not_consume_and_blocks_used(client, db, seed_org):
    """가입 코드 미리검증: 소비하지 않고 상태만 반환. 없는 코드/이미 쓴 코드를 아이디 입력 전에 막는다."""
    org = seed_org["org"]
    _org_admin(db, org)
    admin = _login(client, "principal@test.dev")

    r = client.post(
        f"/api/v1/orgs/{org.id}/students/register",
        json={"count": 1, "class_label": "5-1반", "names": ["코드검증"]},
        headers=auth(admin),
    )
    assert r.status_code == 200, r.text
    join_code = r.json()["issued"][0]["join_code"]

    # 1) 유효한 미사용 코드 → valid, 그리고 코드가 소비되지 않아야 한다(used_at None 유지)
    v1 = client.post("/api/v1/auth/verify-join-code", json={"code": join_code})
    assert v1.status_code == 200, v1.text
    assert v1.json() == {"valid": True, "reason": "ok"}

    from app.models import StudentJoinCode
    from app.core.security import sha256_hash

    row = db.query(StudentJoinCode).filter(
        StudentJoinCode.code_hash == sha256_hash(join_code.strip().upper())
    ).first()
    db.refresh(row)
    assert row.used_at is None  # 미리검증이 코드를 쓰지 않았다

    # 2) 없는 코드 → not_found
    vbad = client.post("/api/v1/auth/verify-join-code", json={"code": "JOIN-ZZZZ-9999"})
    assert vbad.status_code == 200 and vbad.json()["reason"] == "not_found"

    # 3) 실제 가입(활성화)해서 코드 소비 → 다시 검증하면 used 로 막힌다
    act = client.post(
        "/api/v1/auth/activate-student",
        json={"code": join_code, "student_login_id": "codechk1", "nickname": "검증이", "password": "pass1234"},
    )
    assert act.status_code == 200, act.text
    v2 = client.post("/api/v1/auth/verify-join-code", json={"code": join_code})
    assert v2.json() == {"valid": False, "reason": "used"}


def test_roster_includes_pending_signup_codes(client, db, seed_org):
    """코드만 발급하고 아직 가입 안 한 학생도 명단에 '가입 대기'로 나온다(실명 표시).
    이게 없으면 '가입 대기 0'으로 떠 미가입 학생이 안 보인다."""
    org = seed_org["org"]
    _org_admin(db, org)
    admin = _login(client, "principal@test.dev")

    r = client.post(
        f"/api/v1/orgs/{org.id}/students/register",
        json={"count": 2, "class_label": "6-1반", "names": ["대기학생A", "대기학생B"],
              "genders": ["male", "female"]},
        headers=auth(admin),
    )
    assert r.status_code == 200, r.text

    roster = client.get(f"/api/v1/orgs/{org.id}/roster", headers=auth(admin)).json()
    students = roster["students"] if isinstance(roster, dict) else roster
    pending = [s for s in students if s.get("status") == "pending"]
    names = {s["name"] for s in pending}
    assert "대기학생A" in names and "대기학생B" in names  # 실명으로 표시
    # 대기 학생은 아직 프로필이 없어 pending_signup 플래그로 구분
    assert all(s.get("pending_signup") for s in pending)
    # 성별도 실려온다(선생님 입력)
    a = next(s for s in pending if s["name"] == "대기학생A")
    assert a["gender"] == "male"


def test_reissue_join_code_invalidates_old(client, db, seed_org):
    """미가입 학생 코드 재발급: 새 코드는 유효, 옛 코드는 무효. 이미 쓴 코드는 재발급 거부."""
    org = seed_org["org"]
    _org_admin(db, org)
    admin = _login(client, "principal@test.dev")

    reg = client.post(
        f"/api/v1/orgs/{org.id}/students/register",
        json={"count": 1, "class_label": "2-3반", "names": ["잊은학생"]},
        headers=auth(admin),
    ).json()
    old_code = reg["issued"][0]["join_code"]

    # 해당 대기 코드 id 확보(roster의 pending 행 id = 'jc-<id>')
    roster = client.get(f"/api/v1/orgs/{org.id}/roster", headers=auth(admin)).json()
    students = roster["students"] if isinstance(roster, dict) else roster
    pend = next(s for s in students if s.get("status") == "pending" and s["name"] == "잊은학생")
    code_id = pend["id"][3:]  # 'jc-' 제거

    # 재발급 → 새 코드 수령
    rr = client.post(f"/api/v1/orgs/{org.id}/students/join-codes/{code_id}/reissue", headers=auth(admin))
    assert rr.status_code == 200, rr.text
    new_code = rr.json()["issued"][0]["join_code"]
    assert new_code != old_code

    # 옛 코드는 이제 무효(not_found), 새 코드는 유효
    assert client.post("/api/v1/auth/verify-join-code", json={"code": old_code}).json()["reason"] == "not_found"
    assert client.post("/api/v1/auth/verify-join-code", json={"code": new_code}).json() == {"valid": True, "reason": "ok"}

    # 새 코드로 가입 → 그 뒤엔 재발급 거부(이미 사용됨 409)
    act = client.post("/api/v1/auth/activate-student",
                      json={"code": new_code, "student_login_id": "forgot1", "nickname": "잊은이", "password": "pass1234"})
    assert act.status_code == 200, act.text
    rr2 = client.post(f"/api/v1/orgs/{org.id}/students/join-codes/{code_id}/reissue", headers=auth(admin))
    assert rr2.status_code == 409


def test_one_homeroom_per_class_enforced(client, db, seed_org):
    """담임은 반당 1명 — 이미 담임이 있는 반에 다른 교사를 담임으로 배정하면 409."""
    org = seed_org["org"]
    _org_admin(db, org)
    admin = _login(client, "principal@test.dev")

    a = client.post(f"/api/v1/orgs/{org.id}/teachers",
                    json={"name": "에이샘", "email": "a5@t.dev", "class_name": "5-5반",
                          "role": "담임", "teacher_code": "T-AAA111"}, headers=auth(admin))
    assert a.status_code == 200, a.text
    # 같은 반에 다른 교사를 담임으로 → 거부
    b = client.post(f"/api/v1/orgs/{org.id}/teachers",
                    json={"name": "비샘", "email": "b5@t.dev", "class_name": "5-5반",
                          "role": "담임", "teacher_code": "T-BBB222"}, headers=auth(admin))
    assert b.status_code == 409, b.text
    # 보조는 담임과 별개라 허용
    c = client.post(f"/api/v1/orgs/{org.id}/teachers",
                    json={"name": "씨샘", "email": "c5@t.dev", "class_name": "5-5반",
                          "role": "보조", "teacher_code": "T-CCC333"}, headers=auth(admin))
    assert c.status_code == 200, c.text


def test_roster_shows_unassigned_students_to_principal(client, db, seed_org):
    """학급에서 빠진(class_id=None) 학생도 교장 명단에 '미배정'으로 보인다 — 다시 배정할 수 있게.
    (교사의 '학급에서 제외'는 계정 삭제가 아니라 언링크라, 안 보이면 학생이 사라진 것처럼 됨)"""
    org = seed_org["org"]
    _org_admin(db, org)
    admin = _login(client, "principal@test.dev")

    # seed 학생을 학급에서 뺀다(언링크) — 계정은 그대로
    seed_org["student"].class_id = None
    db.commit()

    roster = client.get(f"/api/v1/orgs/{org.id}/roster", headers=auth(admin)).json()
    students = roster["students"] if isinstance(roster, dict) else roster
    row = next((s for s in students if s["id"] == seed_org["student"].id), None)
    assert row is not None, "미배정 학생이 교장 명단에 보여야 함"
    assert not row["cls"]  # 담당 반 없음(미배정)


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
