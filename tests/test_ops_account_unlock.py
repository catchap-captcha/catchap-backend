"""계정 잠금 해제 — '가입되지 않은 아이디' 기록 정리(POST /ops/login-throttles/purge-orphans).

★이 시험이 지키려는 것
  ① 뒤에 사람이 없는 기록만 지운다 — 실제 계정의 실패 카운터를 지우면 잠금 해제 화면에서
     그 사람이 사라진다(운영자가 피해자를 못 찾는다).
  ② 진행 중인 시도는 남긴다 — 지금 두들기고 있는 상대의 카운터를 0으로 돌리면 캡차 요구가
     풀려 ★공격자에게 새 판을 깔아 주는 셈이 된다.
"""

from datetime import datetime, timedelta

from app.models import LoginThrottle
from tests.test_captcha_api import _instructor, _ops, auth


def _throttle(db, identifier: str, *, hours_ago: float, fail: int = 9) -> None:
    """실패 기록 1건 — updated_at 은 Timestamps 기본값(now)이라 만든 뒤 되돌려 놓는다."""
    row = LoginThrottle(identifier=identifier, fail_count=fail)
    db.add(row)
    db.flush()
    row.updated_at = datetime.now() - timedelta(hours=hours_ago)
    db.commit()


def test_purge_removes_only_old_orphan_records(client, db, seed_org):
    otok = _ops(client, db)
    stu = seed_org["student"].student_login_id  # 실제 계정(conftest 시드)

    _throttle(db, "student:__probe__@example.com", hours_ago=72)  # 오래된 탐색 흔적
    _throttle(db, "user:52.78.5.241", hours_ago=48)               # 오래된 인프라 흔적
    _throttle(db, "student:stu9", hours_ago=30)                   # 오래된 오타
    _throttle(db, "student:attacker9", hours_ago=0.5)             # ★진행 중
    _throttle(db, f"student:{stu}", hours_ago=72)                 # ★실제 계정

    r = client.post("/api/v1/ops/login-throttles/purge-orphans", headers=auth(otok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] == 3
    assert body["kept_recent"] == 1
    assert body["min_age_hours"] == 24

    left = {t.identifier for t in db.query(LoginThrottle).all()}
    assert "student:attacker9" in left and f"student:{stu}" in left
    assert not {"student:__probe__@example.com", "user:52.78.5.241", "student:stu9"} & left
    # (운영자 로그인 자체가 user:ops@t.dev 기록을 남긴다 — 실제 계정이라 그대로 있어야 한다)
    assert "user:ops@t.dev" in left


def test_purge_is_ops_only(client, db):
    itok = _instructor(client, db)
    r = client.post("/api/v1/ops/login-throttles/purge-orphans", headers=auth(itok))
    assert r.status_code == 403


def test_purge_on_empty_list_is_a_no_op(client, db):
    """지울 것이 없어도 200 — 운영자가 버튼을 두 번 눌러도 아무 일도 없어야 한다."""
    otok = _ops(client, db)
    r = client.post("/api/v1/ops/login-throttles/purge-orphans", headers=auth(otok))
    assert r.status_code == 200 and r.json()["deleted"] == 0
