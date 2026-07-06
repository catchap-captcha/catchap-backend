"""학생 개인화 데이터가 실제 DB 테이블에서 나오는지 검증.

- PATCH /students/me/profile → nickname 실제 UPDATE + dashboard 반영
- badges → badges/student_badges 실테이블 반영
- daily-quiz → daily_quiz_status 실테이블 반영 (없으면 오늘 행 생성)
- class-ranking → 같은 반 학생 실데이터 반영
"""

from datetime import date, datetime


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


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_patch_profile_updates_nickname_and_dashboard(client, db, seed_org):
    token = _student_token(client, seed_org)

    res = client.patch(
        "/api/v1/students/me/profile",
        json={"nickname": "새별명", "age": 8},
        headers=auth(token),
    )
    assert res.status_code == 200
    assert res.json()["nickname"] == "새별명"

    # DB 행 자체가 바뀌었는지
    db.refresh(seed_org["student"])
    assert seed_org["student"].nickname == "새별명"
    assert seed_org["student"].age == 8

    # 대시보드(홈)에도 즉시 반영
    dash = client.get("/api/v1/students/me/dashboard", headers=auth(token))
    assert dash.status_code == 200
    assert dash.json()["nickname"] == "새별명"

    # 지갑(마이페이지)에도 반영
    wallet = client.get("/api/v1/students/me/wallet", headers=auth(token))
    assert wallet.json()["nickname"] == "새별명"


def test_badges_reflect_student_badges_table(client, db, seed_org):
    from app.models import Badge, StudentBadge

    b1 = Badge(
        name="첫 걸음", description="첫 학습", icon="i", color="#000",
        condition_text="첫 학습", order_no=0,
    )
    b2 = Badge(
        name="계산 왕", description="30문제", icon="i", color="#000",
        condition_text="30문제", order_no=1,
    )
    db.add_all([b1, b2])
    db.flush()
    sb = StudentBadge(
        student_id=seed_org["student"].id,
        badge_id=b1.id,
        earned_at=datetime(2026, 6, 12),
        progress=1.0,
    )
    db.add(sb)
    db.commit()

    token = _student_token(client, seed_org)
    res = client.get("/api/v1/students/me/badges", headers=auth(token))
    assert res.status_code == 200
    body = res.json()
    assert body["earned"] == 1
    assert body["locked"] == 1
    by_name = {b["name"]: b for b in body["badges"]}
    assert by_name["첫 걸음"]["earned"] is True
    assert by_name["첫 걸음"]["foot"] == "6월 12일 획득"  # earned_at 실데이터 기준
    assert by_name["계산 왕"]["earned"] is False

    # student_badges 행을 지우면 earned 감소
    db.delete(sb)
    db.commit()
    res2 = client.get("/api/v1/students/me/badges", headers=auth(token))
    assert res2.json()["earned"] == 0
    assert res2.json()["locked"] == 2

    # 대시보드 배지 카운트도 실테이블 기준
    dash = client.get("/api/v1/students/me/dashboard", headers=auth(token))
    assert dash.json()["badges"] == {"earned": 0, "total": 2}


def test_daily_quiz_reflects_daily_quiz_status(client, db, seed_org):
    from app.models import DailyQuizStatus

    token = _student_token(client, seed_org)

    # 오늘 행이 없으면 생성된다 (모두 todo)
    res = client.get("/api/v1/students/me/daily-quiz", headers=auth(token))
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == len(body["quizzes"]) > 0
    assert body["done"] == 0
    rows = (
        db.query(DailyQuizStatus)
        .filter(
            DailyQuizStatus.student_id == seed_org["student"].id,
            DailyQuizStatus.quiz_date == date.today(),
        )
        .all()
    )
    assert len(rows) == body["total"]

    # DB에서 상태를 바꾸면 응답에 반영된다
    rows[0].status = "done"
    db.commit()
    res2 = client.get("/api/v1/students/me/daily-quiz", headers=auth(token))
    assert res2.json()["done"] == 1
    done_subjects = [q["subject"] for q in res2.json()["quizzes"] if q["status"] == "done"]
    assert done_subjects == [rows[0].subject]

    # 대시보드 today도 daily_quiz_status 기준
    dash = client.get("/api/v1/students/me/dashboard", headers=auth(token))
    assert dash.json()["today"] == {"done": 1, "total": len(rows)}


def test_class_ranking_uses_real_classmates(client, db, seed_org):
    from app.core.security import hash_password
    from app.models import StudentProfile

    mate = StudentProfile(
        organization_id=seed_org["org"].id,
        class_id=seed_org["class"].id,
        student_login_id="stu02",
        student_code="CAT-2222",
        password_hash=hash_password("1234"),
        nickname="친구닉",
        coins=999,
    )
    db.add(mate)
    db.commit()

    token = _student_token(client, seed_org)
    res = client.get("/api/v1/students/me/class-ranking", headers=auth(token))
    assert res.status_code == 200
    body = res.json()
    assert body["class_size"] == 2
    names = [r["name"] for r in body["board"]]
    assert "친구닉" in names  # 같은 반 실데이터 (닉네임만 노출)
    me_row = next(r for r in body["board"] if r["me"])
    assert me_row["name"] == "테스트학생"
    assert me_row["rank"] == 2  # 코인 100 < 999
    assert body["rank"] == 2
