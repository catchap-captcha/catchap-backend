"""기관 코드 재발급 + 만료 차단 검증."""

from datetime import datetime, timedelta

from app.core.security import hash_password
from app.models import Organization, User


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def _org_admin(db, org):
    admin = User(
        email="principal@test.dev", password_hash=hash_password("Password123!"),
        name="교장", role="org_admin", organization_id=org.id,
        email_verified_at=datetime.utcnow(),
    )
    db.add(admin)
    db.commit()
    return admin


def _login(client, email):
    return client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"}).json()["access_token"]


def test_rotate_org_code(client, db, seed_org):
    org = seed_org["org"]
    old_code = org.code
    _org_admin(db, org)
    admin = _login(client, "principal@test.dev")

    r = client.post(f"/api/v1/orgs/{org.id}/rotate-code", headers=auth(admin))
    assert r.status_code == 200, r.text
    new_code = r.json()["code"]
    assert new_code != old_code
    assert r.json()["code_remain_days"] == 365

    db.expire_all()
    org2 = db.get(Organization, org.id)
    assert org2.code == new_code
    assert org2.code_expires_at > datetime.utcnow()

    # 옛 코드로는 검증 실패(valid=False), 새 코드로는 통과(valid=True)
    old_v = client.post("/api/v1/auth/verify-org-code", json={"organization_id": org.id, "code": old_code})
    assert old_v.json()["valid"] is False
    new_v = client.post("/api/v1/auth/verify-org-code", json={"organization_id": org.id, "code": new_code})
    assert new_v.json()["valid"] is True


def test_expired_code_blocks_verify(client, db, seed_org):
    org = seed_org["org"]
    # 앱 쓰기경로(_now = KST)와 같은 규약 — utcnow면 KST 환경에서 실제로는 '1일 9시간
    # 전'이 돼 만료 판정이 9시간 틀려도 통과한다(판별력 상실).
    org.code_expires_at = datetime.now() - timedelta(days=1)  # 이미 만료
    db.commit()
    r = client.post("/api/v1/auth/verify-org-code", json={"organization_id": org.id, "code": org.code})
    assert r.status_code == 400, r.text
    assert "만료" in str(r.json())


def test_org_code_expiry_roundtrips_in_kst(client, db, seed_org):
    """저장한 만료 시각이 응답에 **그대로** 나와야 한다 (9시간 skew 회귀 방지).

    시각 규약 통일(0717) 전에는 `code_expires_at`이 UTC-naive로 저장되고 읽는 쪽에서
    `utc_to_local()`이 +9h 보정했다. 통일하면서 그 보정을 걷어냈으므로, 저장도 KST여야
    한다 — 한쪽만 바꾸면 표시가 9시간 어긋난다(365일짜리라 최장 1년 지속).

    ⚠️ 이 테스트는 **KST 환경에서만 판별력이 있다**. 로컬 TZ가 UTC면 두 규약이 같은 값이라
    회귀를 못 잡는다(그래서 그 환경에선 skip — 통과가 신호인 척하지 않는다).
    """
    import time

    if time.timezone == 0:  # UTC 환경 — KST/UTC가 구분되지 않음
        import pytest
        pytest.skip("로컬 TZ가 UTC라 9시간 skew를 판별할 수 없는 환경")

    org = seed_org["org"]
    _org_admin(db, org)
    admin = _login(client, "principal@test.dev")

    expected = datetime.now() + timedelta(days=365)
    org.code_expires_at = expected
    db.commit()

    r = client.get("/api/v1/teacher/profile", headers=auth(admin))
    if r.status_code != 200:  # 교장 계정이 teacher/profile을 못 보면 orgs 쪽으로
        r = client.get(f"/api/v1/orgs/{org.id}/settings", headers=auth(admin))
    assert r.status_code == 200, r.text

    returned = datetime.fromisoformat(r.json()["code_expires_at"])
    skew_h = abs((returned - expected).total_seconds()) / 3600
    assert skew_h < 0.1, (
        f"저장값과 응답이 {skew_h:.1f}시간 어긋남 — 저장/읽기 규약 불일치. "
        f"저장={expected} 응답={returned}"
    )


def test_rotate_requires_org_admin(client, db, seed_org):
    """교사는 코드 재발급 불가."""
    org = seed_org["org"]
    tok = client.post(
        "/api/v1/auth/login", json={"email": "t1@test.dev", "password": "Password123!"}
    ).json()["access_token"]
    r = client.post(f"/api/v1/orgs/{org.id}/rotate-code", headers=auth(tok))
    assert r.status_code == 403
