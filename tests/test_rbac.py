"""RBAC — 역할별 접근 제한이 API 단계에서 강제되는지 검증.

살아있는 표면(학생·강사·운영자) 기준으로 재편(백엔드 심층 정리 0718).
은퇴 역할(teacher/parent/org_admin)은 로그인은 되지만(심층 정리까지 유지)
살아있는 어떤 보호 자원에도 못 들어가는 '외부인' 취급임을 고정한다 —
은퇴 라우터가 제거돼 옛 자원은 404(라우팅 자체가 없음)가 정상이다.
강사 스코프(자기 강의만)는 test_instructor가 전담한다.
"""


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
    assert client.get("/api/v1/ops/lectures").status_code in (401, 403)
    assert client.get("/api/v1/notifications").status_code in (401, 403)


def test_student_cannot_access_console_api(client, seed_org):
    """학생 토큰으로 콘솔(운영·강의 제작) API 접근 불가."""
    token = _student_token(client, seed_org)
    assert client.get("/api/v1/ops/lectures", headers=auth(token)).status_code == 403
    assert client.get("/api/v1/ops/settings/ai", headers=auth(token)).status_code == 403
    assert client.get("/api/v1/ops/instructors", headers=auth(token)).status_code == 403


def test_retired_teacher_role_is_outsider(client, seed_org):
    """은퇴 역할(교사) 토큰은 살아있는 어떤 보호 자원에도 못 들어간다.

    학생 전용(403)·콘솔 전용(403)·은퇴 라우터(404 — 라우팅 자체가 없다)."""
    token = _teacher_token(client)
    assert client.get("/api/v1/students/me/dashboard", headers=auth(token)).status_code == 403
    assert client.get("/api/v1/ops/lectures", headers=auth(token)).status_code == 403
    assert client.get("/api/v1/teacher/dashboard", headers=auth(token)).status_code == 404


def test_ops_cannot_access_student_personal_api(client, db, seed_org):
    """운영자는 학생 개인(본인 전용) API에 접근 불가 — PII 분리 원칙 유지."""
    from tests.test_captcha_api import _ops

    ops_tok = _ops(client, db)
    assert client.get("/api/v1/students/me/dashboard", headers=auth(ops_tok)).status_code == 403
    assert client.get("/api/v1/students/me/records", headers=auth(ops_tok)).status_code == 403
    # 운영자 콘솔(익명 집계·강의 제작)은 계속 접근 가능
    assert client.get("/api/v1/ops/orgs", headers=auth(ops_tok)).status_code == 200
