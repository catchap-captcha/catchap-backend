"""운영자용 강의 목록 — 강사·코스·문제 상태를 한 표로.

강사용 화면(코스별 트리)을 그대로 쓰던 것을 운영자 관점으로 새로 만든 것.
"""
from tests.test_captcha_api import _ops, auth


def _mk(db, title, uploader, course_id=None, status="active"):
    from app.models import Lecture

    lec = Lecture(
        title=title, subject="IT", course_id=course_id, video_ext=".mp4",
        video_bytes=1, duration_sec=60, status=status, order_no=1, uploaded_by=uploader,
    )
    db.add(lec)
    db.commit()
    return lec


def _q(db, lecture_id, status="active"):
    from app.models import LectureQuestion

    db.add(LectureQuestion(
        lecture_id=lecture_id,
        payload={"prompt": "p", "options": ["a", "b"]},
        answer_index=0, position_sec=10, status=status,
    ))
    db.commit()


def test_admin_list_shows_instructor_and_issues(client, db, seed_org):
    """★운영자가 알아야 할 것 — 누가 올렸나, 어디가 비었나."""
    tok = auth(_ops(client, db))
    a = _mk(db, "문항 있는 강의", "u-kim")
    _q(db, a.id, "active")
    b = _mk(db, "문항 없는 강의", "u-kim")            # 공개 문항 0 → 시청 검증 꺼짐
    c = _mk(db, "미공개만 남은 강의", "u-cho")
    _q(db, c.id, "draft")

    r = client.get("/api/v1/ops/lecture-admin", headers=tok)
    assert r.status_code == 200, r.text
    d = r.json()
    by = {it["title"]: it for it in d["items"]}
    assert by["문항 있는 강의"]["issues"] == []
    assert "noquestion" in by["문항 없는 강의"]["issues"]
    assert "noquestion" in by["미공개만 남은 강의"]["issues"]
    assert "draftleft" in by["미공개만 남은 강의"]["issues"]
    assert d["summary"]["total"] == 3
    # ★문제 있는 것이 위로 — 운영자가 스크롤하지 않게
    assert d["items"][0]["issues"], "문제 있는 강의가 맨 위여야 한다"


def test_admin_list_filters_by_instructor_and_issue(client, db, seed_org):
    """강사가 늘어나면 필터 없이는 못 본다."""
    tok = auth(_ops(client, db))
    _mk(db, "김강사 것", "u-kim")
    _mk(db, "조강사 것", "u-cho")

    only_kim = client.get("/api/v1/ops/lecture-admin?instructor=u-kim", headers=tok).json()
    assert [it["title"] for it in only_kim["items"]] == ["김강사 것"]

    only_issue = client.get("/api/v1/ops/lecture-admin?issue=noquestion", headers=tok).json()
    assert len(only_issue["items"]) == 2, "둘 다 문항이 없다"


def test_admin_list_is_ops_only(client, db, seed_org):
    """강사는 이 목록을 볼 수 없다 — 남의 강의가 다 보인다."""
    r = client.get("/api/v1/ops/lecture-admin")
    assert r.status_code in (401, 403)
