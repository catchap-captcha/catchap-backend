"""삭제된 계정 이력 — GET /ops/operators/deleted · GET /ops/instructors/deleted.

★이 시험이 지키려는 것
  운영자·강사 삭제는 하드 삭제라 users 행이 남지 않는다. 지우기 직전 스냅샷을 감사 로그에
  남겨 두는 것이 "누가 있었는지"를 복원할 수 있는 ★유일한 기록이다. 그 연결이 끊기면
  화면은 조용히 빈 목록이 되고, 삭제 사실 자체가 없었던 것처럼 보인다.
"""

import pytest

from tests.test_captcha_api import _instructor, _ops, auth

# (종류, 목록 경로, 생성 경로) — 운영자·강사가 같은 규칙을 따르는지 한 벌로 검사한다
KINDS = [
    pytest.param("operators", id="operator"),
    pytest.param("instructors", id="instructor"),
]


def _make_and_delete(client, tok, kind: str, *, name: str, email: str) -> str:
    """계정을 만들고 → 중지 → 삭제. 삭제는 중지 상태에서만 되므로 순서가 강제된다."""
    made = client.post(f"/api/v1/ops/{kind}", json={"name": name, "email": email}, headers=auth(tok))
    assert made.status_code == 200, made.text
    acc_id = made.json()["id"]
    client.patch(f"/api/v1/ops/{kind}/{acc_id}", json={"status": "disabled"}, headers=auth(tok))
    r = client.delete(f"/api/v1/ops/{kind}/{acc_id}", headers=auth(tok))
    assert r.status_code == 200, r.text
    return acc_id


@pytest.mark.parametrize("kind", KINDS)
def test_deleted_history_keeps_the_snapshot_of_a_hard_deleted_account(client, db, kind):
    tok = _ops(client, db)
    acc_id = _make_and_delete(client, tok, kind, name="떠난사람", email=f"gone-{kind}@t.dev")

    body = client.get(f"/api/v1/ops/{kind}/deleted", headers=auth(tok)).json()
    row = next(i for i in body["items"] if i["id"] == acc_id)
    assert row["name"] == "떠난사람"
    assert row["email"] == f"gone-{kind}@t.dev"
    assert row["status_before"] == "disabled"  # 중지 후에만 삭제된다
    assert row["deleted_at"]
    assert row["deleted_by"] == "운영자"  # 삭제를 실행한 사람(_ops 헬퍼 계정)

    # 살아 있는 목록에는 없어야 한다 — 두 영역이 섞이면 구분하는 의미가 없다
    live = client.get(f"/api/v1/ops/{kind}", headers=auth(tok)).json()
    assert acc_id not in {o["id"] for o in live}


@pytest.mark.parametrize("kind", KINDS)
def test_deleted_history_is_newest_first(client, db, kind):
    tok = _ops(client, db)
    first = _make_and_delete(client, tok, kind, name="먼저", email=f"first-{kind}@t.dev")
    second = _make_and_delete(client, tok, kind, name="나중", email=f"second-{kind}@t.dev")

    items = client.get(f"/api/v1/ops/{kind}/deleted", headers=auth(tok)).json()["items"]
    ids = [i["id"] for i in items]
    assert ids.index(second) < ids.index(first)


@pytest.mark.parametrize("kind", KINDS)
def test_deleted_history_is_empty_before_any_deletion(client, db, kind):
    tok = _ops(client, db)
    assert client.get(f"/api/v1/ops/{kind}/deleted", headers=auth(tok)).json()["items"] == []


@pytest.mark.parametrize("kind", KINDS)
def test_deleted_history_is_ops_only(client, db, kind):
    itok = _instructor(client, db)
    assert client.get(f"/api/v1/ops/{kind}/deleted", headers=auth(itok)).status_code == 403


def test_instructor_with_leftover_lectures_is_blocked_not_crashed(client, db):
    """강사 삭제 가드 — 남은 강의가 있으면 400으로 막는다.

    ★종전엔 Lecture.instructor_id 를 보다가 AttributeError 로 ★500 이 났다. Lecture 에는
    그 열이 없다(강의는 코스를 통해 강사에 매인다). 가드가 아니라 고장이었고, 그래서
    강사 삭제 자체가 한 번도 되지 않았다 — 삭제 이력이 영영 비어 있던 진짜 이유다.
    """
    from app.models import Lecture

    tok = _ops(client, db)
    inst_id = client.post(
        "/api/v1/ops/instructors", json={"name": "강의보유", "email": "has-lec@t.dev"}, headers=auth(tok)
    ).json()["id"]
    client.patch(f"/api/v1/ops/instructors/{inst_id}", json={"status": "disabled"}, headers=auth(tok))
    # 코스 없이 올린 '미분류 강의' — 코스 수만 세면 놓치는 경우다
    db.add(Lecture(title="남은 강의", subject="수학", video_ext=".mp4", uploaded_by=inst_id))
    db.commit()

    r = client.delete(f"/api/v1/ops/instructors/{inst_id}", headers=auth(tok))
    assert r.status_code == 400, r.text
    assert "강의 1개" in r.json()["detail"]
    # 막혔으면 계정도 이력도 그대로여야 한다(반쯤 지워지면 안 된다)
    assert inst_id in {i["id"] for i in client.get("/api/v1/ops/instructors", headers=auth(tok)).json()}
    assert client.get("/api/v1/ops/instructors/deleted", headers=auth(tok)).json()["items"] == []


def test_operator_and_instructor_histories_do_not_bleed_into_each_other(client, db):
    """★두 목록은 서로의 삭제 기록을 보여주면 안 된다 — 같은 감사 로그 표를 쓰기 때문."""
    tok = _ops(client, db)
    op_id = _make_and_delete(client, tok, "operators", name="운영자였던사람", email="was-op@t.dev")
    in_id = _make_and_delete(client, tok, "instructors", name="강사였던사람", email="was-inst@t.dev")

    ops_ids = {i["id"] for i in client.get("/api/v1/ops/operators/deleted", headers=auth(tok)).json()["items"]}
    inst_ids = {i["id"] for i in client.get("/api/v1/ops/instructors/deleted", headers=auth(tok)).json()["items"]}
    assert op_id in ops_ids and op_id not in inst_ids
    assert in_id in inst_ids and in_id not in ops_ids
