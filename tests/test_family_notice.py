"""가정통신 발송 → 학생·학부모 알림 배선 (기존엔 FamilyMessage만 저장되고 수신 배선 단절)."""

from datetime import datetime

from app.core.security import hash_password


def _login(client, role, email):
    r = client.post("/api/v1/auth/login", json={"role": role, "email": email, "password": "Password123!"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_family_message_creates_notifications_for_student_and_parent(client, db, seed_org):
    from app.models import FamilyMessage, Notification, ParentStudentLink, User

    org, teacher, student = seed_org["org"], seed_org["teacher"], seed_org["student"]

    # 학부모 연결(approved)
    parent = User(
        email="fnparent@test.dev", password_hash=hash_password("Password123!"),
        name="가정통신학부모", role="parent", email_verified_at=datetime.utcnow(),
    )
    db.add(parent)
    db.flush()
    db.add(ParentStudentLink(
        parent_user_id=parent.id, student_id=student.id,
        organization_id=org.id, status="approved",
    ))
    db.commit()

    tok = _login(client, "teacher", "t1@test.dev")
    r = client.post(
        "/api/v1/teacher/family-messages",
        json={"student_ids": [student.id], "message": "오늘 우리 아이가 수학을 참 잘했어요!"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["sent"] == 1

    # 저장(기존 동작)
    assert db.query(FamilyMessage).filter(FamilyMessage.student_id == student.id).count() == 1

    # 학생 알림 생성(배선)
    sn = (
        db.query(Notification)
        .filter(Notification.student_id == student.id, Notification.type == "family_notice")
        .first()
    )
    assert sn is not None and "수학" in sn.message
    assert sn.category == "가정통신"

    # 학부모 알림 생성(배선) — child_id로 자녀 필터
    pn = (
        db.query(Notification)
        .filter(Notification.user_id == parent.id, Notification.type == "family_notice")
        .first()
    )
    assert pn is not None and pn.child_id == student.id

    # 학부모 알림 API로도 조회돼야(수신 확인)
    ptok = _login(client, "parent", "fnparent@test.dev")
    plist = client.get("/api/v1/notifications", headers={"Authorization": f"Bearer {ptok}"})
    assert plist.status_code == 200
    body = plist.json()
    items = body if isinstance(body, list) else body.get("items", [])
    assert any("가정통신" in (i.get("category") or "") for i in items), "학부모 알림 목록에 떠야 한다"
