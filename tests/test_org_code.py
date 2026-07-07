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
    org.code_expires_at = datetime.utcnow() - timedelta(days=1)  # 이미 만료
    db.commit()
    r = client.post("/api/v1/auth/verify-org-code", json={"organization_id": org.id, "code": org.code})
    assert r.status_code == 400, r.text
    assert "만료" in str(r.json())


def test_rotate_requires_org_admin(client, db, seed_org):
    """교사는 코드 재발급 불가."""
    org = seed_org["org"]
    tok = client.post(
        "/api/v1/auth/login", json={"email": "t1@test.dev", "password": "Password123!"}
    ).json()["access_token"]
    r = client.post(f"/api/v1/orgs/{org.id}/rotate-code", headers=auth(tok))
    assert r.status_code == 403
