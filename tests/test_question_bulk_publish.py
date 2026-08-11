"""강의 문항 draft 일괄 공개(bulk-publish) — 개별 PUT과 같은 불변식으로 묶어 올린다.

draft가 active 1~2개 뒤에 대량으로 방치되는 패턴(민서 DB 2026-08-11: 292 draft 중 291
placed)을 '남은 것 일괄 공개'로 푼다. 시점 없는 것·같은 시점 중복(이번 배치 안에서 함께
올라가는 것끼리의 충돌 포함)은 조용히 죽이지 않고 사유별 수로 돌려준다.
"""

from tests.test_captcha_api import _instructor, _ops, auth
from tests.test_lectures import _upload_lecture, media_dir  # noqa: F401 (fixture 재사용)


def _draft(client, tok, lec_id, position_sec, **over):
    body = {
        "position_sec": position_sec,
        "status": "draft",
        "prompt": "강의에서 배운 별의 색은?",
        "options": ["파랑", "빨강", "노랑"],
        "answer_index": 1,
        "explain": "빨강이라고 했다.",
        **over,
    }
    r = client.post(f"/api/v1/ops/lectures/{lec_id}/questions", json=body, headers=auth(tok))
    assert r.status_code == 200, r.text
    return r.json()


def test_bulk_publish_activates_placed_skips_unplaced_and_conflicts(client, db, media_dir):
    tok = _instructor(client, db)
    lec = _upload_lecture(client, tok, title="별의 일생", subject="과학", duration=600).json()
    lid = lec["id"]

    d1 = _draft(client, tok, lid, 30)                    # 공개돼야
    d2 = _draft(client, tok, lid, 60)                    # 공개돼야
    d3 = _draft(client, tok, lid, 0)                     # 미배치(0초) → unplaced skip
    a1 = _draft(client, tok, lid, 90, status="active")   # 이미 공개(90초)
    assert a1["status"] == "active"
    d4 = _draft(client, tok, lid, 90)                    # 90초 중복 → conflict skip
    d5 = _draft(client, tok, lid, 120)                   # 같은 시점 둘 —
    d6 = _draft(client, tok, lid, 120)                   #   하나만 공개, 나머지는 배치 내 conflict

    r = client.post(f"/api/v1/ops/lectures/{lid}/questions/bulk-publish", headers=auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["candidates"] == 6, body                # draft 6개(active a1 제외)
    assert body["published"] == 3, body                 # d1·d2 + d5/d6 중 하나
    assert body["skipped"].get("unplaced") == 1, body   # d3
    assert body["skipped"].get("conflict") == 2, body   # d4 + d5/d6 중 하나

    qs = {x["id"]: x for x in client.get(
        f"/api/v1/ops/lectures/{lid}/questions", headers=auth(tok)).json()}
    assert qs[d1["id"]]["status"] == "active"
    assert qs[d2["id"]]["status"] == "active"
    assert qs[d3["id"]]["status"] == "draft"            # 미배치는 그대로
    assert qs[d4["id"]]["status"] == "draft"            # 중복은 그대로
    # 120초 둘 중 정확히 하나만 공개(배치 내 충돌 방지)
    assert [qs[d5["id"]]["status"], qs[d6["id"]]["status"]].count("active") == 1


def test_bulk_publish_selected_only(client, db, media_dir):
    tok = _instructor(client, db)
    lec = _upload_lecture(client, tok, title="암석", subject="과학", duration=600).json()
    lid = lec["id"]
    d1 = _draft(client, tok, lid, 30)
    d2 = _draft(client, tok, lid, 60)

    r = client.post(
        f"/api/v1/ops/lectures/{lid}/questions/bulk-publish",
        json={"question_ids": [d1["id"]]}, headers=auth(tok),
    )
    assert r.status_code == 200, r.text
    assert r.json()["published"] == 1 and r.json()["candidates"] == 1

    qs = {x["id"]: x for x in client.get(
        f"/api/v1/ops/lectures/{lid}/questions", headers=auth(tok)).json()}
    assert qs[d1["id"]]["status"] == "active"
    assert qs[d2["id"]]["status"] == "draft"            # 선택 안 함 → 그대로


def test_bulk_publish_instructor_only(client, db, media_dir):
    """콘텐츠 저작은 강사 전용 — 운영자는 문항을 공개할 수 없다(감독·검수만)."""
    tok = _instructor(client, db)
    lec = _upload_lecture(client, tok, title="지층", subject="과학", duration=600).json()
    _draft(client, tok, lec["id"], 30)

    otok = _ops(client, db)
    r = client.post(
        f"/api/v1/ops/lectures/{lec['id']}/questions/bulk-publish", headers=auth(otok)
    )
    assert r.status_code == 403, r.text
